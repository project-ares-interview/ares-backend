import os
import tempfile
import json
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timezone
from dotenv import load_dotenv
from tqdm import tqdm

# --- 필요한 라이브러리 임포트  --- 
from azure.storage.blob import BlobServiceClient, BlobClient
from llama_index.core import VectorStoreIndex, Settings, StorageContext, Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.readers.file import PyMuPDFReader
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from llama_index.vector_stores.azureaisearch import AzureAISearchVectorStore, IndexManagement

class AzureBlobRAGSystem:
    """Azure Blob Storage 문서와 Azure AI Search 인덱스를 사용하는 RAG 시스템 (증분 인덱싱 지원)"""

    def __init__(self, container_name: str, index_name: str):
        print(f"🚀 Azure 통합 RAG 시스템 초기화 (컨테이너: {container_name}, AI Search 인덱스: {index_name})...")
        
        self.container_name = container_name
        self.index_name = index_name
        
        self._load_env()
        
        # Azure 서비스 클라이언트 설정
        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        search_key = os.getenv("AZURE_SEARCH_KEY")
        credential = AzureKeyCredential(search_key)
        
        self.blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        self.container_client = self.blob_service_client.get_container_client(self.container_name)
        
        # 문서 조회를 위한 SearchClient
        self.search_client = SearchClient(endpoint=search_endpoint, index_name=self.index_name, credential=credential)
        
        # 인덱스 관리를 위한 SearchIndexClient
        search_index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
        
        self._setup_llamaindex()
        
        try:
            self.vector_store = AzureAISearchVectorStore(
                search_or_index_client=search_index_client,
                index_name=self.index_name,
                id_field_key="id",
                chunk_field_key="chunk",
                embedding_field_key="embedding",
                metadata_string_field_key="metadata",
                doc_id_field_key="doc_id", # 문서 식별을 위해 필수
                index_management=IndexManagement.CREATE_IF_NOT_EXISTS,
            )
            self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
            
            # 쿼리를 위한 인덱스 및 쿼리 엔진 초기화
            self.index = VectorStoreIndex.from_vector_store(self.vector_store)
            self.query_engine = self.index.as_query_engine(similarity_top_k=5)
            print("✅ Azure AI Search VectorStore 및 쿼리 엔진 설정 완료")

        except Exception as e:
            raise ConnectionError(f"Azure AI Search VectorStore 설정 실패: {e}")

    def _sanitize_id(self, name: str) -> str:
        """파일 이름의 특수 문자를 ID로 사용하기 안전하게 변경"""
        return name.replace('[', '_').replace(']', '_')

    def _load_env(self):
        """환경 변수 로드"""
        
        required_vars = ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_KEY', 'AZURE_STORAGE_CONNECTION_STRING', 'AZURE_SEARCH_ENDPOINT', 'AZURE_SEARCH_KEY']
        if not all(os.getenv(var) for var in required_vars):
            raise ValueError("필수 환경 변수가 .env.keys 파일에 모두 설정되지 않았습니다.")

    def _setup_llamaindex(self):
        """LlamaIndex의 LLM, 임베딩 모델 등 설정"""
        print("🔧 LlamaIndex 구성 요소 설정 중...")
        Settings.llm = AzureOpenAI(
            engine=os.getenv('AZURE_OPENAI_MODEL'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            api_key=os.getenv('AZURE_OPENAI_KEY'),
            api_version=os.getenv('API_VERSION', '2024-02-15-preview'),
        )
        Settings.embed_model = AzureOpenAIEmbedding(
            model="text-embedding-3-small",
            deployment_name=os.getenv('AZURE_EMBEDDING_MODEL', 'text-embedding-3-small'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            api_key=os.getenv('AZURE_OPENAI_KEY'),
            api_version='2023-05-15'
        )
        Settings.node_parser = SentenceSplitter(chunk_size=1024, chunk_overlap=200)
        print("  ✅ LlamaIndex 설정 완료")

    def _get_indexed_doc_metadata(self) -> Dict[str, datetime]:
        """AI Search에 저장된 문서들의 파일명과 마지막 수정 시간을 가져온다."""
        print("📊 AI Search에서 기존 인덱스 메타데이터 조회 중...")
        indexed_docs = {}
        try:
            # 'doc_id'와 'metadata' 필드만 선택하여 모든 문서 검색
            results = self.search_client.search(search_text="*", select=["doc_id", "metadata"])
            for doc in results:
                metadata = json.loads(doc.get("metadata", "{}"))
                last_modified_str = metadata.get("last_modified")
                if last_modified_str:
                    # ISO 형식의 문자열을 timezone-aware datetime 객체로 변환
                    indexed_docs[doc["doc_id"]] = datetime.fromisoformat(last_modified_str)
            print(f"  ✅ 총 {len(indexed_docs)}개의 인덱싱된 문서 정보 확인.")
        except Exception as e:
            print(f"  ⚠️ 인덱스 메타데이터 조회 실패 (인덱스가 비어있을 수 있음): {e}")
        return indexed_docs

    def load_documents_from_blob(self, blobs_to_process: List[BlobClient]) -> List[Document]:
        """지정된 Azure Blob 목록에서 문서를 로드하여 LlamaIndex Document 객체로 변환"""
        if not blobs_to_process:
            return []

        documents = []
        pdf_reader = PyMuPDFReader()
        print("📖 지정된 Blob에서 문서 로딩 시작...")

        for blob_client in blobs_to_process:
            blob_name = blob_client.blob_name
            print(f"  🔍 처리 중: {blob_name}")
            
            # 1. with 구문 밖에서 delete=False로 임시 파일 생성
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=Path(blob_name).suffix)
            try:
                downloader = blob_client.download_blob()
                temp_file.write(downloader.readall())
                
                # 2. ★★★가장 중요★★★ 파일을 명시적으로 닫아 잠금을 해제합니다.
                temp_file.close()

                # 3. 이제 잠금이 풀린 파일 경로를 사용하여 안전하게 문서를 로드합니다.
                if blob_name.lower().endswith('.pdf'):
                    loaded_docs = pdf_reader.load_data(file_path=temp_file.name)
                elif blob_name.lower().endswith('.txt'):
                    text_content = Path(temp_file.name).read_text(encoding='utf-8')
                    loaded_docs = [Document(text=text_content)]
                else:
                    continue
                
                last_modified_utc = blob_client.get_blob_properties().last_modified.replace(tzinfo=timezone.utc)

                for doc in loaded_docs:
                    doc.id_ = self._sanitize_id(blob_name) 
                    doc.metadata.update({
                        'file_name': self._sanitize_id(blob_name),
                        'container': self.container_name,
                        'source': 'azure_blob',
                        'last_modified': last_modified_utc.isoformat()
                    })
                documents.extend(loaded_docs)
                print(f"    ✅ '{blob_name}' 로드 완료 ({len(loaded_docs)}개 문서)")

            except Exception as e:
                print(f"    ❌ '{blob_name}' 로드 실패: {e}")
            finally:
                # 4. 작업이 성공하든 실패하든, 임시 파일을 반드시 삭제합니다.
                os.remove(temp_file.name)
        
        print(f"✅ 총 {len(documents)}개 문서 로드 완료.")
        return documents


    def delete_doc(self, doc_id: str):
        """AI Search에서 특정 문서 ID와 관련된 모든 청크를 삭제"""
        print(f"🗑️ 인덱스에서 '{doc_id}' 문서 삭제 중...")
        try:
            # ref_doc_id를 사용하여 관련 노드(청크) 삭제
            self.index.delete_ref_doc(ref_doc_id=doc_id, delete_from_docstore=True)
            print(f"  ✅ '{doc_id}' 문서 삭제 완료.")
        except Exception as e:
            print(f"  ❌ '{doc_id}' 문서 삭제 실패: {e}")

    def sync_index(self, company_name_filter: str = None):
        """Blob Storage와 AI Search 인덱스를 동기화 (추가/업데이트/삭제)"""
        print("\n" + "="*60)
        print("🔄 Azure AI Search 인덱스 동기화 시작...")
        
        # 1. 소스(Blob Storage)의 현재 상태 가져오기
        source_blobs = {}
        for blob in self.container_client.list_blobs():
            if blob.name.endswith(('.pdf', '.txt')):
                if company_name_filter:
                    # Construct the expected prefix for the company
                    expected_prefix = f"[{company_name_filter}]"
                    if not blob.name.startswith(expected_prefix):
                        continue # Skip blobs that don't match the company filter
                source_blobs[blob.name] = blob.last_modified.replace(tzinfo=timezone.utc)
        
        # 2. 대상(AI Search)의 현재 상태 가져오기
        indexed_docs = self._get_indexed_doc_metadata()
        
        # 3. 변경 사항 계산
        source_files = set(source_blobs.keys())
        indexed_files = set(indexed_docs.keys())
        
        files_to_add = source_files - indexed_files
        files_to_delete = indexed_files - source_files
        files_to_check = indexed_files.intersection(source_files)
        
        files_to_update = {
            fname for fname in files_to_check 
            if source_blobs[fname] > indexed_docs[fname]
        }
        
        # 4. 변경 사항 적용
        # (4-1) 삭제
        if files_to_delete:
            print(f"\n {len(files_to_delete)}개의 삭제할 파일을 발견했습니다.")
            for fname in files_to_delete:
                self.delete_doc(fname)
        
        # (4-2) 업데이트 (삭제 후 추가)
        if files_to_update:
            print(f"\n🔄 {len(files_to_update)}개의 업데이트할 파일을 발견했습니다.")
            for fname in files_to_update:
                self.delete_doc(fname) # 기존 버전 삭제

        # (4-3) 추가 (새 파일 + 업데이트된 파일)
        files_to_process = files_to_add.union(files_to_update)
        if files_to_process:
            print(f"\n➕ {len(files_to_process)}개의 추가/업데이트할 파일을 처리합니다.")
            blob_clients_to_process = [
                self.container_client.get_blob_client(fname) for fname in files_to_process
            ]
            
            new_documents = self.load_documents_from_blob(blob_clients_to_process)
            
            if new_documents:
                print("⚡ 새로운 문서를 벡터 인덱스에 추가하는 중...")
                # ✅ 수정된 부분: for 반복문으로 문서를 하나씩 insert 합니다.
                for doc in tqdm(new_documents, desc="새 문서 인덱싱"):
                    self.index.insert(doc)
                print("  ✅ 새로운 문서 추가 완료.")
        
        if not any([files_to_add, files_to_delete, files_to_update]):
            print("\n✅ 인덱스가 이미 최신 상태입니다. 변경 사항이 없습니다.")
        
        print("="*60 + "\n")

    def query(self, question: str) -> str:
        """Azure AI Search 인덱스를 사용하여 질문에 답변"""
        print(f"🔍 '{self.index_name}' 인덱스에서 질문 처리: {question[:50]}...")
        try:
            response = self.query_engine.query(question)
            return str(response)
        except Exception as e:
            return f"❌ 질문 처리 실패: {e}"

# --- 시스템 테스트를 위한 실행 코드 ---
def test_azure_rag_system():
    print("=" * 60)
    print("🚀 Azure 통합 RAG 시스템(Blob Storage + AI Search) 종합 테스트")
    print("=" * 60)
    
    try:
        rag_system = AzureBlobRAGSystem(
            container_name='interview-data', 
            index_name='sk-hynix-report-index'
        )

        # 인덱스 동기화 실행
        rag_system.sync_index()

        # 질문 테스트
        test_questions = [
            "삼성전자의 주요 사업 부문은 무엇인가요?",
            "회사의 향후 전망은 어떻습니까?"
        ]
        
        print("\n🎯 사업 현황 질문 테스트:")
        for i, question in enumerate(test_questions, 1):
            print(f"\n[Q{i}] {question}")
            answer = rag_system.query(question)
            print(f"[A{i}] {answer}")
        
        print("\n🎉 테스트 완료!")
            
    except Exception as e:
        print(f"❌ 시스템 테스트 중 심각한 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_azure_rag_system()
