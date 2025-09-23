# ares/api/views/v1/resume_analysis.py
import json
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.parsers import MultiPartParser, JSONParser
from drf_spectacular.utils import extend_schema

# Models
from ares.api.models.user import Profile

# Serializers
from ares.api.serializers.v1.resume_analysis import (
    ResumeAnalysisInSerializer,
    CompanyDataSerializer,
    ResumeAnalysisOutSerializer,
)

# Services
from ares.api.services import ocr_service, resume_service

# Utils (👈 추가)
from ares.api.utils.ai_utils import chat_complete
from ares.api.utils.text_utils import ensure_full_text

_END_SENTINEL = "<<END_OF_REPORT>>"
User = get_user_model()


class ResumeAnalysisAPIView(APIView):
    parser_classes = [MultiPartParser, JSONParser]
    permission_classes = [permissions.AllowAny]

    def _refine_text_with_llm(self, raw_text: str, context_type: str) -> str:
        """
        LLM을 사용하여 OCR 결과 등 원본 텍스트를 정제합니다.
        - 자동-이어받기(chat_complete)로 중간 끊김 방지
        - 종료 시그널 강제(_END_SENTINEL)
        - ensure_full_text()로 RAW와 병합 보정
        """
        raw_text = (raw_text or "")
        if not raw_text.strip():
            return ""

        # === 프롬프트 규칙(종료 시그널 & 닫힘 보장) ===
        rule_tail = (
            "\n\n[엄격 규칙]\n"
            f"1) 출력 마지막 줄에 {_END_SENTINEL} 를 '단독 줄'로 반드시 출력한다.\n"
            "2) 마크다운 코드펜스/리스트/표는 모두 닫고 마무리한다.\n"
            "3) 요약하지 말고, 제공된 내용의 '불필요한 잡음 제거 및 구조화'에 집중한다.\n"
        )

        # === context_type 별 시스템/유저 프롬프트 ===
        if context_type == "resume":
            system_prompt = (
                "You are an expert career coach. Your task is to clean and structure raw resume text. "
                "Preserve all substantive content related to work experience, projects, education, skills, awards, and certifications. "
                "Remove only true OCR errors, irrelevant document headers/footers (e.g., 'Page 1 of 2'), watermarks, or any other non-content boilerplate. "
                "Do not summarize or remove any actual experience or skill descriptions. "
                "Maintain the original detail and formatting as much as possible, correcting only obvious errors."
                + rule_tail
            )
            user_prompt_template = (
                "Here is the raw resume text:\n\n```\n{text_chunk}\n```\n\n"
                "Please clean and structure this resume text. Preserve all substantive content and remove only irrelevant document artifacts.\n"
                f"End the output with {_END_SENTINEL} on its own line."
            )
        elif context_type == "job description":
            system_prompt = (
                "You are an expert HR assistant. Your task is to extract only the essential job description content from raw text. "
                "Focus on job responsibilities, required skills, qualifications, and preferred qualifications. "
                "Remove all irrelevant information such as application periods, company boilerplate, recruitment process details, "
                "contact information, website footers/headers, and any other non-core job description text. "
                "Maintain the original formatting of the extracted relevant content as much as possible."
                + rule_tail
            )
            user_prompt_template = (
                "Here is the raw job description text:\n\n```\n{text_chunk}\n```\n\n"
                "Please extract only the core job description. Ensure to remove all extraneous details.\n"
                f"End the output with {_END_SENTINEL} on its own line."
            )
        elif context_type == "research material":
            system_prompt = (
                "You are an expert research assistant. Your task is to extract relevant information from raw research material. "
                "Focus on details about the company, its business, industry trends, market position, recent news, strategic initiatives, "
                "and any information pertinent to the job role or industry. "
                "Remove all irrelevant content such as advertisements, navigation menus, website footers/headers, disclaimers, "
                "unrelated articles, or any other non-substantive text. "
                "Maintain the original formatting of the extracted relevant content as much as possible."
                + rule_tail
            )
            user_prompt_template = (
                "Here is a part of the raw research material text:\n\n```\n{text_chunk}\n```\n\n"
                "Please extract only the core relevant information about the company, industry, or job role from this chunk. "
                "Ensure to remove all extraneous details and summarize concisely if necessary to fit the context window. "
                "Avoid repeating information already extracted in previous chunks if possible.\n"
                f"End the output with {_END_SENTINEL} on its own line."
            )
        else:  # Fallback
            system_prompt = (
                "You are a helpful assistant. Your task is to refine the provided text by removing irrelevant information and formatting issues."
                + rule_tail
            )
            user_prompt_template = "Please refine the following text:\n\n```\n{text_chunk}\n```\n\n" \
                                   f"End the output with {_END_SENTINEL} on its own line."

        # === 대용량 리서치 전용 청크링 ===
        if context_type == "research material" and len(raw_text) > 30000:
            from ares.api.utils.common_utils import chunk_text
            MAX_CHARS_PER_CHUNK = 30000
            CHUNK_OVERLAP = 500

            chunks = list(chunk_text(raw_text, MAX_CHARS_PER_CHUNK, CHUNK_OVERLAP))
            parts = []
            for i, chunk in enumerate(chunks):
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt_template.format(text_chunk=chunk)}
                ]
                try:
                    # chat_complete: 자동-이어받기, 종료 시그널 검사
                    extracted = chat_complete(
                        messages=messages,
                        temperature=0.2,
                        max_tokens=2000,
                        max_cont=2,
                        require_sentinel=True,
                    ) or ""
                    # 시그널 제거
                    extracted = extracted.replace(_END_SENTINEL, "").strip()
                    if extracted:
                        parts.append(extracted)
                except Exception as e:
                    print(f"[warn] LLM refinement failed for {context_type} chunk {i+1}: {e}. Skipping chunk.")

            refined_joined = "\n\n".join(parts)
            # 최종: RAW와 병합 보정(끊김/코드펜스 균형)
            safe_refined = ensure_full_text(refined_joined, raw_text)
            return safe_refined if safe_refined else raw_text

        # === 단일 청크 처리 ===
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt_template.format(text_chunk=raw_text)}
        ]
        try:
            refined_text = chat_complete(
                messages=messages,
                temperature=0.2,
                max_tokens=2000,
                max_cont=2,
                require_sentinel=True,
            ) or ""
            refined_text = refined_text.replace(_END_SENTINEL, "").strip()

            # 최종: RAW와 병합 보정
            safe_refined = ensure_full_text(refined_text, raw_text)
            return safe_refined if safe_refined else raw_text
        except Exception as e:
            print(f"[warn] LLM refinement failed for {context_type}: {e}. Returning raw text.")
            return raw_text

    @extend_schema(
        summary="Analyze Resume against Job Description",
        description="""
Takes a resume, a job description (JD), and optional company research material.
It performs a detailed analysis of the resume's fit for the job, providing scores and feedback.
The input can be either text or file uploads.
""",
        request=ResumeAnalysisInSerializer,
        responses=ResumeAnalysisOutSerializer
    )
    def post(self, request, *args, **kwargs):
        serializer = ResumeAnalysisInSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # 1) company 정보 파싱 및 검증
        # - 입력 타입 호환: dict 또는 JSON 문자열 모두 허용
        # - 키 호환: "company"와 "company_name" 모두 수용하되 내부적으로 company_name으로 통일
        raw_company = data.get("company") or data.get("company_name") or {}

        if isinstance(raw_company, str):
            try:
                company_data = json.loads(raw_company or "{}")
            except json.JSONDecodeError:
                return Response({"error": "Company data is not a valid JSON string."},
                                status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(raw_company, dict):
            company_data = raw_company
        else:
            company_data = {}

        # 키 정규화: company_name 기준으로 맞춤
        company_name = company_data.get("company_name") or company_data.get("company") or ""
        if company_name and "company_name" not in company_data:
            company_data["company_name"] = company_name

        company_serializer = CompanyDataSerializer(data=company_data)
        if not company_serializer.is_valid():
            return Response({"company_errors": company_serializer.errors},
                            status=status.HTTP_400_BAD_REQUEST)
        company_meta = company_serializer.validated_data


        # 2) JD, 이력서, 리서치 텍스트 추출 (파일 또는 텍스트)
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

        # 3) 텍스트 정제 (끊김 방지 + RAW 병합 보정 포함)
        refined_jd_text = self._refine_text_with_llm(raw_jd_text, "job description")
        refined_resume_text = self._refine_text_with_llm(raw_resume_text, "resume")
        refined_research_text = self._refine_text_with_llm(raw_research_text, "research material")

        # 4) Service 호출하여 분석 수행 (정제된 텍스트 사용)
        analysis_result = resume_service.analyze_all(
            jd_text=refined_jd_text,
            resume_text=refined_resume_text,
            research_text=refined_research_text,
            company_meta=company_meta
        )

        # 5) 분석 결과를 Profile에 저장 (개발/테스트 편의성)
        try:
            user = None
            if request.user and request.user.is_authenticated:
                user = request.user
            else:
                # 로그인하지 않은 경우, 테스트용으로 ID 1번 유저를 사용
                user = User.objects.filter(id=1).first()

            if user:
                profile, created = Profile.objects.get_or_create(user=user)
                profile.jd_context = refined_jd_text
                profile.resume_context = refined_resume_text
                profile.research_context = refined_research_text
                profile.save()
            else:
                print("[WARNING] No user found to save contexts to profile.")
        except Exception as e:
            print(f"[WARNING] Failed to save contexts to user profile: {e}")


        # 6) 다음 단계 활용을 위해 입력 컨텍스트 포함
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
        """파일 또는 텍스트 필드에서 본문 추출(OCR 포함)."""
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
