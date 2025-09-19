# ares/api/views/v1/interview/admin.py
import os
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from unidecode import unidecode

from ares.api.services.rag.new_azure_rag_llamaindex import AzureBlobRAGSystem
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)

class InterviewAdminSyncIndexAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company = (request.data or {}).get("company_name", "")
        if not company:
            return Response({"error": "company_name 필드가 필요합니다."}, status=400)

        container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")
        safe_company_name = unidecode(company.lower()).replace(" ", "-")
        index_name = f"{safe_company_name}-report-index"

        try:
            rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            rag_system.sync_index(company_name_filter=company)
        except Exception as e:
            log.exception("[admin_sync] 인덱스 동기화 실패")
            return Response({"error": f"동기화 실패: {e}"}, status=500)

        return Response({"message": "동기화 완료", "index": index_name}, status=200)
