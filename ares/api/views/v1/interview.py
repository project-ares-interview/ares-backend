# ares/api/views/interview.py
from __future__ import annotations

import logging
import os
import traceback
import uuid
from typing import Any
from uuid import uuid4

from django.shortcuts import render
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from unidecode import unidecode

# Serializers
from ares.api.serializers.v1.interview import (
    InterviewStartIn,
    InterviewStartOut,
    InterviewNextIn,
    InterviewNextOut,
    InterviewAnswerIn,
    InterviewFinishIn,
    InterviewFinishOut,
)

# Services and Utils
from ares.api.services.company_data import find_affiliates_by_keyword
from ares.api.services.rag.final_interview_rag import RAGInterviewBot

# 직렬화 안전 변환기 및 공용 유틸
from ares.api.utils.common_utils import get_logger
from ares.api.utils.state_utils import to_jsonable

# DB Models
from ares.api.models import InterviewSession, InterviewTurn

try:
    from ares.api.utils.search_utils import search_ncs_hybrid
except ImportError:
    search_ncs_hybrid = None  # 선택적 의존 (없어도 동작)

log = get_logger(__name__)

# =========================
# 내부 유틸
# =========================
def _reqid() -> str:
    return uuid4().hex[:8]


def _normalize_difficulty(x: str | None) -> str:
    m = {
        "easy": "easy",
        "normal": "normal",
        "hard": "hard",
        "쉬움": "easy",
        "보통": "normal",
        "어려움": "hard",
    }
    return m.get((x or "normal").lower(), "normal")


def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta:
        return ""
    if (q := (meta.get("ncs_query") or "").strip()):
        return q
    role = (meta.get("role") or meta.get("job_title") or "").strip()
    company = (meta.get("company") or meta.get("name") or "").strip()
    return f"{company} {role}".strip()


def _make_ncs_context(meta: dict[str, Any] | None) -> dict[str, Any]:
    q = _ncs_query_from_meta(meta)
    if not q or not search_ncs_hybrid:
        return {"ncs": [], "ncs_query": q}
    try:
        items = search_ncs_hybrid(q) or []
        compact = [
            {
                "code": it.get("ncs_code"),
                "title": it.get("title"),
                "desc": it.get("summary"),
            }
            for it in items
        ]

        compact = [it for it in compact if it.get("title") or it.get("code") or it.get("desc")]

        return {"ncs": compact, "ncs_query": q}
    except Exception as e:
        log.warning(f"[NCS] hybrid search failed ({e})")
        return {"ncs": [], "ncs_query": q}


FALLBACK_QUESTION = (
    "기아의 생산운영/공정기술 관점에서 효율화가 필요하다고 판단한 영역을 한 가지 선정해, "
    "개선 아이디어와 기대 효과(예: 리드타임, 불량률, 설비가동률 지표)를 근거와 함께 설명해 주시겠습니까?"
)


def _extract_first_question_from_plan(interview_plan_data: dict | list | None) -> str | None:
    """
    설계된 인터뷰 플랜에서 첫 질문 1개를 방어적으로 추출한다.
    기대 구조 (예): {"interview_plan": [{"stage": "...", "questions": ["..."]}, ...]}
    """
    if not interview_plan_data:
        return None

    plan_list = None
    if isinstance(interview_plan_data, dict):
        plan_list = interview_plan_data.get("interview_plan")
    elif isinstance(interview_plan_data, list):
        plan_list = interview_plan_data
    else:
        return None

    if not isinstance(plan_list, list) or not plan_list:
        return None

    first_stage = plan_list[0]
    if not isinstance(first_stage, dict):
        return None

    questions = first_stage.get("questions")
    if isinstance(questions, list) and questions:
        q0 = questions[0]
        if isinstance(q0, str) and q0.strip():
            return q0.strip()
    return None


# =========================
# Views
# =========================
class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        keyword = (request.data or {}).get("keyword", "")
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        rid = _reqid()
        try:
            s = InterviewStartIn(data=request.data)
            s.is_valid(raise_exception=True)
            v = s.validated_data

            meta = v.get("meta") or {}
            company_name = (meta.get("company") or "").strip()
            job_title = (meta.get("job_title") or "").strip()
            if not company_name or not job_title:
                return Response(
                    {"error": "meta 정보에 company와 job_title이 모두 필요합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 인덱스/컨테이너 이름 산출
            safe_company_name = unidecode(company_name.lower()).replace(" ", "-")
            index_name = f"{safe_company_name}-report-index"
            container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")

            difficulty = _normalize_difficulty(v.get("difficulty"))
            interviewer_mode = v.get("interviewer_mode", "team_lead")

            # NCS 컨텍스트(없어도 진행)
            ncs_ctx = v.get("ncs_context") or _make_ncs_context(meta)

            log.info(
                f"[{rid}] 🧠 {company_name} 맞춤 면접 계획 설계 (난이도:{difficulty}, 면접관:{interviewer_mode})"
            )
            log.info(f"[{rid}] 🔎 [QUERY_RAW] company={company_name}, job_title={job_title}, index={index_name}")

            # RAG Bot 준비
            try:
                rag_bot = RAGInterviewBot(
                    company_name=company_name,
                    job_title=job_title,
                    container_name=container_name,
                    index_name=index_name,
                    difficulty=difficulty,
                    interviewer_mode=interviewer_mode,
                    ncs_context=ncs_ctx,
                    jd_context=v.get("jd_context", ""),
                    resume_context=v.get("resume_context", ""),
                    research_context=v.get("research_context", ""),
                )
            except Exception:
                log.exception(f"[{rid}] RAGInterviewBot 초기화 실패")
                return Response(
                    {"error": "RAG 시스템 초기화 실패. Azure/Search 설정을 확인해주세요."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if not getattr(rag_bot, "rag_ready", True):
                # 서비스 내부에서 준비 여부를 노출한다면 체크
                return Response(
                    {"error": "RAG 시스템이 준비되지 않았습니다. Azure 설정을 확인해주세요."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # 인터뷰 플랜 설계 (예외 흡수)
            interview_plan_data: dict | None = None
            try:
                interview_plan_data = rag_bot.design_interview_plan() or {}
            except Exception:
                log.exception(f"[{rid}] 인터뷰 플랜 설계 중 예외")
                interview_plan_data = {}

            # 첫 질문 추출 실패 시 폴백 사용
            question_text = _extract_first_question_from_plan(
                interview_plan_data.get("interview_plan") if isinstance(interview_plan_data, dict) else interview_plan_data
            ) or FALLBACK_QUESTION

            if question_text == FALLBACK_QUESTION:
                log.warning(f"[{rid}] 플랜은 생성됐거나 비어있음. 첫 질문 추출 실패 → 폴백 질문 사용")

            log.info(f"[{rid}] ✅ 구조화 면접 계획 수립 완료")

            # 세션 저장용 컨텍스트(너무 큰 객체는 피하고 핵심만)
            rag_context_to_save = {
                "interview_plan": interview_plan_data or {},
                "company_name": company_name,
                "job_title": job_title,
                "container_name": container_name,
                "index_name": index_name,
            }

            # 직렬화 안전화
            meta_safe = to_jsonable(meta)
            ncs_ctx_safe = to_jsonable(ncs_ctx)
            rag_context_safe = to_jsonable(rag_context_to_save)

            # DB: 세션/첫 턴 생성
            session = InterviewSession.objects.create(
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                jd_context=v.get("jd_context", ""),
                resume_context=v.get("resume_context", ""),
                ncs_query=ncs_ctx_safe.get("ncs_query", ""),
                meta=meta_safe,
                context=ncs_ctx_safe,
                rag_context=rag_context_safe,
                language=(v.get("language") or "ko").lower(),
                difficulty=difficulty,
                interviewer_mode=interviewer_mode,
            )
            turn = InterviewTurn.objects.create(
                session=session,
                turn_index=0,
                role=InterviewTurn.Role.INTERVIEWER,
                question=question_text,
            )

            out_payload = {
                "message": "Interview session started successfully.",
                "question": question_text,
                "session_id": str(session.id),
                "turn_index": int(turn.turn_index),
                "context": session.context or {},
                "language": session.language,
                "difficulty": session.difficulty,
                "interviewer_mode": session.interviewer_mode,
            }
            out = InterviewStartOut(out_payload)
            return Response(out.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            log.error(f"[{rid}] InterviewStart ERROR: {e}\n{traceback.format_exc()}")
            return Response(
                {"error": str(e), "trace": traceback.format_exc()[:2000], "reqid": rid},
                status=status.HTTP_400_BAD_REQUEST,
            )


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        rid = _reqid()
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=status.HTTP_404_NOT_FOUND)

        last_cand = (
            session.turns.filter(role=InterviewTurn.Role.CANDIDATE)
            .order_by("-turn_index")
            .first()
        )
        if not last_cand:
            return Response(
                {"error": "꼬리질문을 생성할 이전 답변이 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rag_info = session.rag_context or {}
        interview_plan_list = (
            (rag_info.get("interview_plan") or {}).get("interview_plan", [])
            if isinstance(rag_info, dict)
            else []
        )

        current_stage = "N/A"
        current_objective = "N/A"
        for stage in interview_plan_list:
            if isinstance(stage, dict) and last_cand.question in stage.get("questions", []):
                current_stage = stage.get("stage", "N/A")
                current_objective = stage.get("objective", "N/A")
                break

        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(next)")
            rag_bot = None

        analysis = last_cand.scores or {}
        followup_q = None
        if rag_bot:
            try:
                followup_q = rag_bot.generate_follow_up_question(
                    last_cand.question or "",
                    last_cand.answer or "",
                    analysis,
                    current_stage,
                    current_objective,
                )
            except Exception:
                log.exception(f"[{rid}] follow-up 생성 중 예외")
                followup_q = None

        followups = (
            [followup_q]
            if (followup_q and isinstance(followup_q, str) and followup_q.strip())
            else ["이전 답변에서 근거·수치·사례 한 가지를 더 구체적으로 설명해주시겠습니까?"]
        )

        last = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last.turn_index + 1 if last else 0),
            role=InterviewTurn.Role.INTERVIEWER,
            question="",
            followups=followups,
        )

        out = InterviewNextOut(
            {"session_id": str(session.id), "turn_index": int(turn.turn_index), "followups": followups}
        )
        return Response(out.data, status=status.HTTP_200_OK)


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        rid = _reqid()
        s = InterviewAnswerIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response(
                {"error": "RAG 컨텍스트가 없는 세션입니다. 면접을 다시 시작해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(answer)")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            analysis_result = rag_bot.analyze_answer_with_rag(v.get("question", ""), v["answer"])
        except Exception:
            log.exception(f"[{rid}] 답변 분석 중 예외")
            return Response(
                {"error": "답변 분석 중 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if isinstance(analysis_result, dict) and "error" in analysis_result:
            return Response(analysis_result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        last_turn = session.turns.order_by("-turn_index").first()
        InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        return Response(analysis_result, status=status.HTTP_200_OK)


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 이미 종료되었습니다."}, status=status.HTTP_404_NOT_FOUND)

        session.report_id = f"report-{session.id}"
        session.status = InterviewSession.Status.FINISHED
        session.finished_at = timezone.now()
        session.save(update_fields=["report_id", "status", "finished_at"])

        out = InterviewFinishOut({"report_id": session.report_id, "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class InterviewReportAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response(
                {"error": "RAG 컨텍스트가 없는 세션이므로 리포트를 생성할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
                resume_context=session.resume_context,
            )
        except Exception:
            log.exception("[report] RAGInterviewBot 초기화 실패")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않아 리포트를 생성할 수 없습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            resume_feedback_analysis = rag_bot.analyze_resume_with_rag()
        except Exception:
            log.exception("[report] 이력서 분석 중 예외")
            resume_feedback_analysis = {}

        transcript = []
        turns = (
            session.turns.filter(role=InterviewTurn.Role.CANDIDATE)
            .order_by("turn_index")
            .all()
        )
        interview_plan_list = (
            (rag_info.get("interview_plan") or {}).get("interview_plan", [])
            if isinstance(rag_info, dict)
            else []
        )

        q_idx = 0
        for stage in interview_plan_list:
            if not isinstance(stage, dict):
                continue
            q_list = stage.get("questions", []) or []
            for _ in q_list:
                if q_idx < len(turns):
                    turn = turns[q_idx]
                    transcript.append(
                        {
                            "question_id": f"Q{q_idx + 1}",
                            "stage": stage.get("stage", "N/A"),
                            "question": turn.question,
                            "answer": turn.answer,
                            "analysis": turn.scores,
                        }
                    )
                    q_idx += 1

        # 남은 턴은 Follow-up으로
        for i, turn in enumerate(turns[q_idx:], start=q_idx):
            transcript.append(
                {
                    "question_id": f"Follow-up {i + 1}",
                    "stage": "Follow-up",
                    "question": turn.question,
                    "answer": turn.answer,
                    "analysis": turn.scores,
                }
            )

        try:
            final_report_data = rag_bot.generate_final_report(
                transcript, rag_info.get("interview_plan", {}), resume_feedback_analysis
            )
        except Exception:
            log.exception("[report] 최종 리포트 생성 중 예외")
            final_report_data = {
                "error": "리포트 생성 중 오류가 발생했습니다.",
                "transcript": transcript,
                "resume_feedback": resume_feedback_analysis,
            }

        return Response(final_report_data, status=status.HTTP_200_OK)


def interview_coach_view(request):
    """Renders the AI Interview Coach page."""
    return render(request, "api/interview_coach.html")
