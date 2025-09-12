import os
import json
import requests
from django.conf import settings
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery


class AzureNCSRetriever:
    def __init__(self):
        self.search_endpoint = getattr(settings, "AZURE_SEARCH_ENDPOINT", None)
        self.search_api_key = getattr(settings, "AZURE_SEARCH_KEY", None)
        self.index_name = "ncs-index"

        self.openai_endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", None)
        self.openai_api_key = getattr(settings, "AZURE_OPENAI_API_KEY", None)

        self.deployment_name_A = getattr(settings, "AZURE_EMB_DEPLOYMENT_NAME_A", None)
        self.deployment_name_B = getattr(settings, "AZURE_EMB_DEPLOYMENT_NAME_B", None)
        self.deployment_name_C = getattr(settings, "AZURE_EMB_DEPLOYMENT_NAME_C", None)
        
        self.content_field_name = "content_concat"
        self.vector_field_A = "content_vector"
        self.vector_field_B = "content_vector"
        self.vector_field_C = "content_vector"

        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.search_api_key)
        )
        print("✅ Azure Retriever (Multi-Model)가 성공적으로 준비되었습니다.")

   
    def _get_embedding_from_azure(self, text: str, deployment_name: str) -> list[float]:
        """
        주어진 텍스트와 '배포 이름'을 사용하여, 해당하는 Azure OpenAI 엔드포인트에서
        벡터 임베딩을 받아오는 재사용 가능한 함수입니다.
        """
        url = (
            f"{self.openai_endpoint}openai/deployments/{deployment_name}"
            f"/embeddings?api-version=2023-05-15"
        )
        headers = {'Content-Type': 'application/json', 'api-key': self.openai_api_key}
        data = {"input": text}
        
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        
        return response.json()['data'][0]['embedding']

    def search(self, job_title: str, k: int = 2) -> str:
        """3개의 Azure 배포 모델로 각각 쿼리 벡터를 생성하고, 동시에 검색을 요청합니다."""
        print(f"'{job_title}'에 대한 멀티-모델 검색을 시작합니다...")
        
        try:
            query_vector_a = self._get_embedding_from_azure(job_title, self.deployment_name_A)
            query_vector_b = self._get_embedding_from_azure(job_title, self.deployment_name_B)
            query_vector_c = self._get_embedding_from_azure(job_title, self.deployment_name_C)
        except Exception as e:
            print(f"❌ 임베딩 생성 실패: {e}")
            return f"오류: 임베딩을 가져오는 데 실패했습니다."

        vector_query_a = VectorizedQuery(vector=query_vector_a, k_nearest_neighbors=k, fields=self.vector_field_A)
        vector_query_b = VectorizedQuery(vector=query_vector_b, k_nearest_neighbors=k, fields=self.vector_field_B)
        vector_query_c = VectorizedQuery(vector=query_vector_c, k_nearest_neighbors=k, fields=self.vector_field_C)

        try:
            results = self.search_client.search(
                search_text=None,
                vector_queries=[vector_query_a, vector_query_b, vector_query_c],
                select=[self.content_field_name]
            )
            
            retrieved_texts = {result[self.content_field_name] for result in results}
            
            if not retrieved_texts:
                return "관련 직무 정보를 찾을 수 없습니다."
                
            return "\n\n---\n\n".join(retrieved_texts)
            
        except Exception as e:
            print(f"❌ Azure AI Search 검색 실패: {e}")
            return f"오류: Azure AI Search에서 검색하는 중 문제가 발생했습니다."
