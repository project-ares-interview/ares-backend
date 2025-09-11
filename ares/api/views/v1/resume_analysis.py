# ares/api/views/v1/resume_analysis.py
import json
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.parsers import MultiPartParser, JSONParser

# Serializers
from ares.api.serializers.v1.resume_analysis import ResumeAnalysisInSerializer, CompanyDataSerializer

# Services
from ares.api.services import ocr_service, resume_service


class ResumeAnalysisAPIView(APIView):
    parser_classes = [MultiPartParser, JSONParser]
    permission_classes = [permissions.AllowAny] # 로그인 없이 접근 허용

    def post(self, request, *args, **kwargs):
        serializer = ResumeAnalysisInSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # 1. company 정보 파싱 및 검증
        try:
            company_data = json.loads(data.get("company", "{}"))
            company_serializer = CompanyDataSerializer(data=company_data)
            if not company_serializer.is_valid():
                return Response({"company_errors": company_serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
            company_meta = company_serializer.validated_data
        except json.JSONDecodeError:
            return Response({"error": "Company data is not a valid JSON string."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. JD, 이력서, 리서치 자료 텍스트 추출 (파일 또는 텍스트)
        try:
            jd_text = self._get_text(data, "jd")
            resume_text = self._get_text(data, "resume")
            research_text = self._get_text(data, "research", required=False)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if not jd_text:
            return Response({"error": "JD is required (jd_file or jd_text)."}, status=status.HTTP_400_BAD_REQUEST)
        if not resume_text:
            return Response({"error": "Resume is required (resume_file or resume_text)."}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Service 호출하여 분석 수행
        result = resume_service.analyze_all(
            jd_text=jd_text,
            resume_text=resume_text,
            research_text=research_text,
            company_meta=company_meta
        )

        return Response(result, status=status.HTTP_200_OK)

    def _get_text(self, data, prefix, required=True):
        """Helper to get text from either file or text field."""
        file = data.get(f"{prefix}_file")
        text = data.get(f"{prefix}_text")

        if file:
            try:
                file_bytes = file.read()
                content_type = file.content_type or "application/octet-stream"
                return ocr_service.di_analyze_bytes(file_bytes, content_type=content_type)
            except Exception as e:
                raise ValueError(f"Failed to process {prefix}_file: {e}")
        elif text:
            return text
        elif not required:
            return ""
        else:
            raise ValueError(f"{prefix}_file or {prefix}_text is required.")
