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
    permission_classes = [permissions.AllowAny]

    def _refine_text_with_llm(self, raw_text: str, context_type: str) -> str:
        """
        LLM을 사용하여 OCR 결과 등 원본 텍스트를 정제합니다.
        """
        if not raw_text.strip():
            return ""

        # Define prompts based on context_type
        system_prompt = ""
        user_prompt_template = ""

        if context_type == "resume":
            system_prompt = (
                "You are an expert career coach. Your task is to clean and structure raw resume text. "
                "Preserve all substantive content related to work experience, projects, education, skills, awards, and certifications. "
                "Remove only true OCR errors, irrelevant document headers/footers (e.g., 'Page 1 of 2'), watermarks, or any other non-content boilerplate. "
                "Do not summarize or remove any actual experience or skill descriptions. "
                "Maintain the original detail and formatting as much as possible, correcting only obvious errors."
            )
            user_prompt_template = (
                "Here is the raw resume text:\n\n```\n{text_chunk}\n```\n\n"
                "Please clean and structure this resume text. Preserve all substantive content and remove only irrelevant document artifacts."
            )
        elif context_type == "job description":
            system_prompt = (
                "You are an expert HR assistant. Your task is to extract only the essential job description content from raw text. "
                "Focus on job responsibilities, required skills, qualifications, and preferred qualifications. "
                "Remove all irrelevant information such as application periods, company boilerplate, recruitment process details, "
                "contact information, website footers/headers, and any other non-core job description text. "
                "Maintain the original formatting of the extracted relevant content as much as possible."
            )
            user_prompt_template = (
                f"Here is the raw {context_type} text:\n\n```\n{{text_chunk}}\n```\n\n"
                f"Please extract only the core {context_type}. Ensure to remove all extraneous details."
            )
        elif context_type == "research material":
            system_prompt = (
                "You are an expert research assistant. Your task is to extract relevant information from raw research material. "
                "Focus on details about the company, its business, industry trends, market position, recent news, strategic initiatives, "
                "and any information pertinent to the job role or industry. "
                "Remove all irrelevant content such as advertisements, navigation menus, website footers/headers, disclaimers, "
                "unrelated articles, or any other non-substantive text. "
                "Maintain the original formatting of the extracted relevant content as much as possible."
            )
            user_prompt_template = (
                "Here is a part of the raw research material text:\n\n```\n{text_chunk}\n```\n\n"
                "Please extract only the core relevant information about the company, industry, or job role from this chunk. "
                "Ensure to remove all extraneous details and summarize concisely if necessary to fit the context window. "
                "Avoid repeating information already extracted in previous chunks if possible."
            )
        else: # Fallback for any other context_type
            system_prompt = "You are a helpful assistant. Your task is to refine the provided text by removing irrelevant information and formatting issues."
            user_prompt_template = "Please refine the following text:\n\n```\n{text_chunk}\n```"

        # Handle large research material by chunking
        if context_type == "research material" and len(raw_text) > 30000: # Heuristic for large text
            from ares.api.utils.common_utils import chunk_text
            MAX_CHARS_PER_CHUNK = 30000 # Roughly 7500 tokens for Korean
            CHUNK_OVERLAP = 500

            chunks = list(chunk_text(raw_text, MAX_CHARS_PER_CHUNK, CHUNK_OVERLAP))
            extracted_parts = []
            for i, chunk in enumerate(chunks):
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt_template.format(text_chunk=chunk)}
                ]
                try:
                    from ares.api.utils.ai_utils import chat
                    extracted_chunk_text = chat(messages=messages, temperature=0.2, max_tokens=2000)
                    if extracted_chunk_text:
                        extracted_parts.append(extracted_chunk_text)
                except Exception as e:
                    print(f"Error: LLM refinement failed for {context_type} chunk {i+1}: {e}. Skipping chunk.")

            refined_text = "\n\n".join(extracted_parts)
            return refined_text if refined_text else raw_text
        # Process as a single chunk
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt_template.format(text_chunk=raw_text)}
        ]
        try:
            from ares.api.utils.ai_utils import chat
            refined_text = chat(messages=messages, temperature=0.2, max_tokens=2000)
            return refined_text if refined_text else raw_text
        except Exception as e:
            print(f"Error: LLM refinement failed for {context_type}: {e}. Returning raw text.")
            return raw_text

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
            raw_jd_text = self._get_text(data, "jd")
            raw_resume_text = self._get_text(data, "resume")
            raw_research_text = self._get_text(data, "research", required=False)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if not raw_jd_text:
            return Response({"error": "JD is required (jd_file or jd_text)."}, status=status.HTTP_400_BAD_REQUEST)
        if not raw_resume_text:
            return Response({"error": "Resume is required (resume_file or resume_text)."}, status=status.HTTP_400_BAD_REQUEST)

        # 3. 텍스트 정제
        refined_jd_text = self._refine_text_with_llm(raw_jd_text, "job description")
        refined_resume_text = self._refine_text_with_llm(raw_resume_text, "resume")
        refined_research_text = self._refine_text_with_llm(raw_research_text, "research material")

        # 4. Service 호출하여 분석 수행 (정제된 텍스트 사용)
        analysis_result = resume_service.analyze_all(
            jd_text=refined_jd_text,
            resume_text=refined_resume_text,
            research_text=refined_research_text,
            company_meta=company_meta
        )

        # 5. 다음 면접 단계에서 활용할 수 있도록 입력 컨텍스트를 응답에 포함
        # NOTE: resume_service.analyze_all이 구조화된 NCS 데이터를 반환한다고 가정합니다.
        #       실제로는 서비스 로직 수정이 필요할 수 있습니다.
        structured_ncs_context = analysis_result.get("ncs_context", {})

        analysis_result["input_contexts"] = {
            "raw": {
                "jd_context": raw_jd_text,
                "resume_context": raw_resume_text,
                "research_context": raw_research_text,
            },
            "refined": {
                "jd_context": refined_jd_text,
                "resume_context": refined_resume_text,
                "research_context": refined_research_text,
                "meta": company_meta,
                "ncs_context": structured_ncs_context
            }
        }

        return Response(analysis_result, status=status.HTTP_200_OK)

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
