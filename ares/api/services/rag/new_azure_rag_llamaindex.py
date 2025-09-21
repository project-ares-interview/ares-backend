# ares/api/services/rag/new_azure_rag_llamaindex.py
import os
import io
import json
import hashlib
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone

from tqdm import tqdm
from bs4 import BeautifulSoup
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient, BlobClient, ContentSettings
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from xhtml2pdf import pisa

from llama_index.core import VectorStoreIndex, Settings, StorageContext, Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.readers.file import PyMuPDFReader
from llama_index.vector_stores.azureaisearch import AzureAISearchVectorStore, IndexManagement

from ares.api.services.dart_service import DartService
from ares.api.services.blob_storage import BlobStorage
from ares.api.services.company_data import get_company_dart_name_map



# ============================== 메타 저장소 ==============================
class _MetaStore:
    """
    간단한 파일 기반 메타 저장소.
    구조:
    {
      "<blob_name>": {
        "etag": "...",
        "sha256": "...",
        "last_modified": "2024-09-01T09:00:00+00:00"
      },
      ...
    }
    """
    def __init__(self, path: Optional[str] = None):
        default_path = ".rag_meta.json"
        self.path = path or os.getenv("AZURE_RAG_META_PATH", default_path)
        self._data: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            else:
                self._data = {}
        except Exception as e:
            print(f"⚠️ 메타 저장소 로드 실패({self.path}): {e} → 새로 생성합니다.")
            self._data = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ 메타 저장소 저장 실패({self.path}): {e}")

    def get(self, key: str) -> Optional[Dict[str, str]]:
        return self._data.get(key)

    def set(self, key: str, value: Dict[str, str]):
        self._data[key] = value

    def delete(self, key: str):
        if key in self._data:
            del self._data[key]

    def keys(self):
        return list(self._data.keys())


# ============================== 유틸 ==============================
def _sanitize_id(name: str) -> str:
    """파일명을 인덱스 doc_id로 사용할 때 안전하게 변환"""
    return name.replace("[", "_").replace("]", "_")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _calc_sha256_streaming(stream: io.BufferedReader, chunk_size: int = 1024 * 1024) -> str:
    """대용량 파일에 대해 스트리밍으로 sha256 계산"""
    h = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


# ============================== 본체 ==============================
class AzureBlobRAGSystem:
    """Azure Blob + Azure AI Search 기반 RAG 시스템 (증분 인덱싱: ETag/sha256/메타 저장)"""

    def __init__(self, container_name: str, index_name: str):
        print(f"🚀 Azure 통합 RAG 시스템 초기화 (컨테이너: {container_name}, 인덱스: {index_name})...")
        self.container_name = container_name
        self.index_name = index_name
        self.query_engine = None

        self._require_env([
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_KEY",
            "AZURE_STORAGE_CONNECTION_STRING",
            "AZURE_SEARCH_ENDPOINT",
            "AZURE_SEARCH_KEY",
        ])

        # Azure 클라이언트
        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        search_key = os.getenv("AZURE_SEARCH_KEY")
        credential = AzureKeyCredential(search_key)

        self.blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        self.container_client = self.blob_service_client.get_container_client(self.container_name)

        self.search_client = SearchClient(endpoint=search_endpoint, index_name=self.index_name, credential=credential)
        self.search_index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)

        # DART 연동 및 기업 데이터 서비스
        try:
            self.dart_service = DartService()
            self.blob_storage = BlobStorage()
            self.company_data = get_company_dart_name_map()
            print("✅ DART 서비스 및 기업 데이터 로드 완료")
        except Exception as e:
            print(f"⚠️ DART 서비스 또는 기업 데이터 초기화 실패: {e}")
            self.dart_service = None
            self.company_data = {}


        # LlamaIndex 설정
        self._setup_llamaindex()

        # 벡터 스토어/인덱스/쿼리엔진
        try:
            self.vector_store = AzureAISearchVectorStore(
                search_or_index_client=self.search_index_client,
                index_name=self.index_name,
                id_field_key="id",
                chunk_field_key="chunk",
                embedding_field_key="embedding",
                metadata_string_field_key="metadata",
                doc_id_field_key="doc_id",
                index_management=IndexManagement.CREATE_IF_NOT_EXISTS,
            )
            self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
            self.index = VectorStoreIndex.from_vector_store(self.vector_store)
            self.query_engine = self.index.as_query_engine(similarity_top_k=5)
            print("✅ Azure AI Search VectorStore 및 쿼리 엔진 설정 완료")
        except Exception as e:
            raise ConnectionError(f"Azure AI Search VectorStore 설정 실패: {e}")

        # 메타 저장소
        self.meta_store = _MetaStore()

    def is_ready(self) -> bool:
        """RAG 시스템이 쿼리를 수행할 준비가 되었는지 확인"""
        return self.query_engine is not None

    # -------------------------- 내부 설정 --------------------------
    def _require_env(self, keys: List[str]):
        missing = [k for k in keys if not os.getenv(k)]
        if missing:
            raise ValueError(f"필수 환경 변수 누락: {', '.join(missing)}")

    def _setup_llamaindex(self):
        print("🔧 LlamaIndex 구성 요소 설정 중...")
        Settings.llm = AzureOpenAI(
            engine=os.getenv("AZURE_OPENAI_MODEL"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_KEY"),
            api_version=os.getenv("API_VERSION", "2024-02-15-preview"),
        )
        
        # --- [DEBUG] 임베딩 설정 값 출력 ---
        embedding_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        embedding_key = os.getenv("AZURE_OPENAI_KEY")
        embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        print("\n--- [DEBUG] Azure OpenAI Embedding Settings ---")
        print(f"  - ENDPOINT: {embedding_endpoint}")
        print(f"  - API KEY: {'*' * (len(embedding_key) - 4) + embedding_key[-4:] if embedding_key else 'Not Set'}")
        print(f"  - DEPLOYMENT: {embedding_deployment}")
        print("---------------------------------------------\n")
        
        Settings.embed_model = AzureOpenAIEmbedding(
            model=embedding_deployment,
            deployment_name=embedding_deployment,
            azure_endpoint=embedding_endpoint,
            api_key=embedding_key,
            api_version=os.getenv("API_VERSION", "2024-02-15-preview"),
        )
        Settings.node_parser = SentenceSplitter(chunk_size=1024, chunk_overlap=200)
        print("  ✅ LlamaIndex 설정 완료")

    # -------------------------- 인덱스 메타 조회 --------------------------
    def _get_indexed_doc_metadata(self) -> Dict[str, Dict[str, str]]:
        """
        AI Search 인덱스에 이미 들어있는 문서의 메타데이터를 가져온다.
        반환: { doc_id: {"last_modified": "...", "etag": "...", "sha256": "..."} }
        """
        print("📊 AI Search 인덱스 메타데이터 조회 중...")
        indexed: Dict[str, Dict[str, str]] = {}
        try:
            # metadata는 stringified JSON으로 저장되어 있음
            results = self.search_client.search(search_text="*", select=["doc_id", "metadata"])
            for doc in results:
                try:
                    meta = json.loads(doc.get("metadata", "{}"))
                    indexed[doc["doc_id"]] = {
                        "last_modified": meta.get("last_modified", ""),
                        "etag": meta.get("etag", ""),
                        "sha256": meta.get("sha256", ""),
                    }
                except Exception:
                    continue
            print(f"  ✅ 인덱스에 {len(indexed)}개 문서 메타 수집.")
        except Exception as e:
            print(f"  ⚠️ 인덱스 메타데이터 조회 실패(비어있을 수 있음): {e}")
        return indexed

    # -------------------------- Blob → Document 로딩 --------------------------
    def _download_to_temp_and_hash(self, blob_client: BlobClient) -> Tuple[str, str]:
        """
        Blob을 임시파일로 저장하고 sha256을 계산한다.
        반환: (temp_filepath, sha256_hex)
        """
        props = blob_client.get_blob_properties()
        total = props.size or None

        # 임시파일 생성
        suffix = Path(blob_client.blob_name).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name

        sha256 = hashlib.sha256()
        try:
            stream = blob_client.download_blob()
            # 스트리밍으로 읽어 임시파일에 쓰고 동시에 해시 계산
            for chunk in stream.chunks():
                if not isinstance(chunk, (bytes, bytearray)):
                    chunk = chunk.readall()
                tmp.write(chunk)
                sha256.update(chunk)
            tmp.close()
            return tmp_path, sha256.hexdigest()
        except Exception as e:
            try:
                tmp.close()
            except Exception:
                pass
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise RuntimeError(f"Blob 다운로드/해시 계산 실패: {blob_client.blob_name}, {e}")

    def load_documents_from_blob(self, blobs_to_process: List[BlobClient]) -> List[Document]:
        """
        지정된 Blob 목록에서 문서를 로드하여 LlamaIndex Document 객체로 변환.
        각 Document.metadata에 file_name/container/source/last_modified/etag/sha256 포함.
        """
        if not blobs_to_process:
            return []

        documents: List[Document] = []
        pdf_reader = PyMuPDFReader()
        print("📖 지정된 Blob에서 문서 로딩 시작...")

        for blob_client in blobs_to_process:
            blob_name = blob_client.blob_name
            print(f"  🔍 처리 중: {blob_name}")

            try:
                props = blob_client.get_blob_properties()
                etag = getattr(props, "etag", "") or ""
                last_modified = props.last_modified.replace(tzinfo=timezone.utc)

                # 다운로드 + 해시
                temp_path, sha256_hex = self._download_to_temp_and_hash(blob_client)

                try:
                    # 파일 타입에 따라 로딩
                    if blob_name.lower().endswith(".pdf"):
                        loaded_docs = pdf_reader.load_data(file_path=temp_path)
                    elif blob_name.lower().endswith(".txt"):
                        text = Path(temp_path).read_text(encoding="utf-8", errors="ignore")
                        loaded_docs = [Document(text=text)]
                    elif blob_name.lower().endswith(".xml"):
                        # XML 파일 처리: BeautifulSoup으로 텍스트만 추출
                        raw_content = Path(temp_path).read_bytes()
                        soup = BeautifulSoup(raw_content, 'lxml')
                        text = soup.get_text(separator='\n', strip=True)
                        loaded_docs = [Document(text=text)]
                    else:
                        print("    ℹ️ 지원하지 않는 확장자. 스킵:", blob_name)
                        continue

                    # 메타데이터 부여
                    for doc in loaded_docs:
                        doc.id_ = _sanitize_id(blob_name)
                        doc.metadata.update({
                            "file_name": _sanitize_id(blob_name),
                            "container": self.container_name,
                            "source": "azure_blob",
                            "last_modified": _iso(last_modified),
                            "etag": etag,
                            "sha256": sha256_hex,
                        })

                    documents.extend(loaded_docs)
                    print(f"    ✅ '{blob_name}' 로드 완료 ({len(loaded_docs)}개 문서)")

                finally:
                    # 임시파일 제거
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

            except Exception as e:
                print(f"    ❌ '{blob_name}' 로드 실패: {e}")

        print(f"✅ 총 {len(documents)}개 문서 로드 완료.")
        return documents

    # -------------------------- 삭제 --------------------------
    def delete_doc(self, doc_id: str):
        """AI Search에서 특정 문서 ID와 관련된 모든 청크 삭제"""
        print(f"🗑️ 인덱스에서 '{doc_id}' 문서 삭제 중...")
        try:
            self.index.delete_ref_doc(ref_doc_id=doc_id, delete_from_docstore=True)
            print(f"  ✅ '{doc_id}' 삭제 완료.")
        except Exception as e:
            print(f"  ❌ '{doc_id}' 삭제 실패: {e}")

    # -------------------------- 동기화 --------------------------
    def _ensure_latest_report_from_dart(self, company_name: str):
        """DART API를 통해 최신 사업보고서를 확인하고, 없으면 다운로드하여 Blob Storage에 업로드"""
        if not self.dart_service or not self.company_data:
            print("  ⚠️ DART 서비스가 초기화되지 않아 최신 보고서 확인을 건너뜁니다.")
            return

        print(f"🎯 DART에서 '{company_name}'의 최신 사업보고서 확인 중...")
        
        exact_company_name = self.company_data.get(company_name)
        if not exact_company_name:
            print(f"  ⚠️ 기업 데이터에서 '{company_name}'을(를) 찾을 수 없습니다.")
            return

        corp_code = self.dart_service.get_corp_code(exact_company_name)
        if not corp_code:
            print(f"  ⚠️ DART에서 '{exact_company_name}'의 기업 코드를 찾을 수 없습니다.")
            return

        report_info = self.dart_service.get_latest_business_report_info(corp_code)
        if not report_info:
            print(f"  ℹ️ '{exact_company_name}'의 최신 사업보고서 정보를 찾을 수 없습니다.")
            return

        rcept_no = report_info.get("rcept_no")
        if not rcept_no:
            print(f"  ⚠️ 보고서 정보에 접수번호가 없습니다: {report_info}")
            return

        blob_name_xml = f"[{company_name}]사업보고서_{rcept_no}.xml"
        if self.blob_storage.blob_exists(blob_name_xml):
            print(f"  ✅ 최신 사업보고서 '{blob_name_xml}'이(가) 이미 Blob Storage에 존재합니다.")
            return

        print(f"  📥 '{blob_name_xml}' 다운로드 및 업로드 시작...")
        xml_content_bytes = self.dart_service.download_document(rcept_no)
        if xml_content_bytes:
            try:
                self.blob_storage.upload_blob(blob_name_xml, xml_content_bytes, "application/xml")
                print(f"  ✅ '{blob_name_xml}'을(를) Blob Storage에 성공적으로 업로드했습니다.")
            except Exception as e:
                print(f"  ❌ Blob Storage 업로드 실패: {e}")
        else:
            print(f"  ❌ DART에서 문서 다운로드 실패 (접수번호: {rcept_no})")


    def sync_index(self, company_name_filter: Optional[str] = None):
        """
        Blob ↔ 인덱스 증분 동기화.
        - DART API를 통해 최신 보고서가 없으면 다운로드 (필터링 시)
        - 추가/변경 판단 기준: ETag + sha256 (둘 중 하나라도 변경 시 업데이트)
        - 메타 저장소와도 동기화 (로컬 JSON)
        - 삭제: Blob/메타/인덱스 간 불일치 정리
        """
        print("\n" + "=" * 64)
        print("🔄 Azure AI Search 인덱스 증분 동기화 시작...")

        # DART API를 통해 최신 사업보고서 확인 및 다운로드 (필터링 시)
        if company_name_filter:
            self._ensure_latest_report_from_dart(company_name_filter)

        # 0) 소스 나열
        source_blobs: Dict[str, Dict[str, str]] = {}
        for blob in self.container_client.list_blobs():
            name = blob.name
            # DART에서 다운로드한 xml 파일도 처리 대상에 포함
            if not name.lower().endswith((".pdf", ".txt", ".xml")):
                continue
            if company_name_filter:
                expected_prefix = f"[{company_name_filter}]"
                if not name.startswith(expected_prefix):
                    continue
            source_blobs[name] = {
                "last_modified": _iso(blob.last_modified.replace(tzinfo=timezone.utc)),
                "etag": getattr(blob, "etag", "") or "",
            }

        # 1) 인덱스/메타 저장소 현황
        indexed_docs = self._get_indexed_doc_metadata()            # {doc_id: {...}}
        meta_keys = set(self.meta_store.keys())                    # blob_name 집합
        source_set = set(source_blobs.keys())
        indexed_set = set(indexed_docs.keys())

        # 2) 삭제 대상
        to_delete_in_index = indexed_set - source_set              # 인덱스에는 있으나 Blob에 없는 것
        to_delete_in_meta = meta_keys - source_set                 # 메타에는 있으나 Blob에 없는 것

        if to_delete_in_index:
            print(f"\n🧹 Blob에 없는 {len(to_delete_in_index)}개를 인덱스에서 삭제합니다.")
            for fname in to_delete_in_index:
                self.delete_doc(fname)

        if to_delete_in_meta:
            print(f"\n🧹 Blob에 없는 {len(to_delete_in_meta)}개를 메타 저장소에서 정리합니다.")
            for fname in to_delete_in_meta:
                self.meta_store.delete(fname)

        # 3) 변경/추가 판정
        to_process: List[str] = []
        for fname, src_meta in source_blobs.items():
            src_etag = src_meta.get("etag", "")
            meta_entry = self.meta_store.get(fname) or {}
            meta_etag = meta_entry.get("etag", "")
            meta_sha = meta_entry.get("sha256", "")

            # 인덱스 메타(참고)
            idx_entry = indexed_docs.get(fname) or {}
            idx_etag = idx_entry.get("etag", "")
            idx_sha = idx_entry.get("sha256", "")

            # 우선순위: ETag가 다르면 다운로드/해시 후 비교 → sha 변경 여부 최종판정
            if src_etag and src_etag == meta_etag and meta_sha and meta_sha == idx_sha:
                # 소스 ETag = 메타 ETag = 인덱스 sha 동일 → 스킵
                continue
            # ETag 불일치 또는 sha 미기록 → 처리 대상
            to_process.append(fname)

        # 4) 추가/업데이트 처리
        if to_process:
            print(f"\n➕ {len(to_process)}개 파일 인덱싱(추가/업데이트) 처리.")
            blob_clients: List[BlobClient] = [self.container_client.get_blob_client(n) for n in to_process]
            new_docs = self.load_documents_from_blob(blob_clients)

            if new_docs:
                print("⚡ 문서 벡터 인덱스에 upsert 중...")
                node_parser = Settings.node_parser
                for doc in new_docs:
                    print(f"  - 문서 '{doc.id_}' 노드 분할 중...")
                    nodes = node_parser.get_nodes_from_documents([doc])
                    print(f"  - '{doc.id_}'에서 {len(nodes)}개의 노드 생성. 50개씩 배치하여 인덱싱합니다.")
                    
                    # 50개씩 배치로 인덱싱
                    batch_size = 50
                    for i in tqdm(range(0, len(nodes), batch_size), desc=f"'{doc.id_}' 인덱싱"):
                        batch = nodes[i:i+batch_size]
                        self.index.insert_nodes(batch)
                    
                    print(f"  - 문서 '{doc.id_}' 인덱싱 완료.")

                # 메타 저장소 업데이트 (Blob props 기반 + 로딩 메타 기반)
                for doc in new_docs:
                    fname = doc.metadata.get("file_name") or doc.id_
                    self.meta_store.set(fname, {
                        "etag": doc.metadata.get("etag", ""),
                        "sha256": doc.metadata.get("sha256", ""),
                        "last_modified": doc.metadata.get("last_modified", ""),
                    })
                self.meta_store.save()
                print("  ✅ 인덱싱 및 메타 저장소 업데이트 완료.")
        else:
            print("\n✅ 변경 사항 없음. 인덱스 최신 상태.")

        print("=" * 64 + "\n")

    # -------------------------- 질의 --------------------------
    def query(self, question: str) -> str:
        """Azure AI Search 인덱스를 사용해 질의"""
        print(f"🔍 '{self.index_name}' 인덱스 질의: {question[:64]}...")
        try:
            response = self.query_engine.query(question)
            return str(response)
        except Exception as e:
            return f"❌ 질문 처리 실패: {e}"


# ============================== 테스트 실행 (선택) ==============================
def test_azure_rag_system():
    print("=" * 60)
    print("🚀 Azure 통합 RAG 시스템(Blob Storage + AI Search) 종합 테스트")
    print("=" * 60)

    try:
        rag_system = AzureBlobRAGSystem(
            container_name="interview-data",
            index_name="sk-hynix-report-index",
        )

        # 인덱스 동기화 실행
        rag_system.sync_index(company_name_filter=None)

        # 질문 테스트
        test_questions = [
            "삼성전자의 주요 사업 부문은 무엇인가요?",
            "회사의 향후 전망은 어떻습니까?",
        ]

        print("\n🎯 사업 현황 질문 테스트:")
        for i, q in enumerate(test_questions, 1):
            print(f"\n[Q{i}] {q}")
            a = rag_system.query(q)
            print(f"[A{i}] {a}")

        print("\n🎉 테스트 완료!")

    except Exception as e:
        print(f"❌ 시스템 테스트 중 심각한 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_azure_rag_system()

