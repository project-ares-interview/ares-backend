# ares/api/views/interview.py
import logging
import uuid
import os
from typing import Any

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import render
from unidecode import unidecode

# Serializers
from ares.api.serializers.v1.interview import (
    InterviewStartIn, InterviewStartOut,
    InterviewNextIn, InterviewNextOut,
    InterviewAnswerIn, InterviewFinishIn, InterviewFinishOut,
)

# Services and Utils
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services.company_data import find_affiliates_by_keyword

# DB Models
from ares.api.models import InterviewSession, InterviewTurn

try:
    from ares.api.utils.search_utils import search_ncs_hybrid
except ImportError:
    search_ncs_hybrid = None

log = logging.getLogger(__name__)


# ===== 내부 유틸 =====
def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta: return ""
    if (q := (meta.get("ncs_query") or "").strip()): return q
    role = (meta.get("role") or meta.get("job_title") or "").strip()
    company = (meta.get("company") or meta.get("name") or "").strip()
    return f"{company} {role}"

def _normalize_difficulty(x: str | None) -> str:
    m = {"easy": "easy", "normal": "normal", "hard": "hard", "쉬움": "easy", "보통": "normal", "어려움": "hard"}
    return m.get((x or "normal").lower(), "normal")

def _make_ncs_context(meta: dict[str, Any] | None, top_k: int = 5) -> dict[str, Any]:
    q = _ncs_query_from_meta(meta)
    if not q or not search_ncs_hybrid: return {"ncs": [], "ncs_query": q}
    try:
        items = search_ncs_hybrid(q, top_k=top_k) or []
        compact = [{"code": it.get("ncs_code"), "title": it.get("title"), "desc": it.get("summary")} for it in items]
        return {"ncs": compact, "ncs_query": q}
    except Exception as e:
        log.warning(f"[NCS] hybrid search failed ({e})")
        return {"ncs": [], "ncs_query": q}


# ===== Views =====

class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        keyword = request.data.get("keyword", "")
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)

        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewStartIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        meta = v.get("meta", {})
        company_name = meta.get("company", "")
        job_title = meta.get("job_title", "")

        if not company_name or not job_title:
            return Response({"error": "meta 정보에 company와 job_title이 모두 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        safe_company_name = unidecode(company_name.lower()).replace(" ", "-")
        index_name = f"{safe_company_name}-report-index"
        container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")

        difficulty = _normalize_difficulty(v.get("difficulty"))
        interviewer_mode = v.get("interviewer_mode", "team_lead")
        ncs_ctx = v.get("ncs_context", {}) or _make_ncs_context(meta)

        rag_bot = RAGInterviewBot(
            company_name=company_name, job_title=job_title,
            container_name=container_name, index_name=index_name,
            difficulty=difficulty, interviewer_mode=interviewer_mode,
            ncs_context=ncs_ctx, jd_context=v["jd_context"],
            resume_context=v["resume_context"], research_context=v.get("research_context", "")
        )

        if not rag_bot.rag_ready:
            return Response({"error": "RAG 시스템이 준비되지 않았습니다. Azure 설정을 확인해주세요."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        interview_plan_data = rag_bot.design_interview_plan()
        interview_plan = interview_plan_data.get("interview_plan", [])
        question_text = ""
        if interview_plan and interview_plan[0].get("questions"):
            question_text = interview_plan[0]["questions"][0]

        if not question_text:
            return Response({"error": "RAG 기반 질문 생성에 실패했습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        rag_context_to_save = {
            "interview_plan": interview_plan_data,
            "company_name": company_name, "job_title": job_title,
            "container_name": container_name, "index_name": index_name,
        }

        session = InterviewSession.objects.create(
            user=request.user if request.user.is_authenticated else None,
            jd_context=v["jd_context"], resume_context=v["resume_context"],
            ncs_query=ncs_ctx.get("ncs_query", ""), meta=meta, context=ncs_ctx,
            rag_context=rag_context_to_save, language=(v.get("language") or "ko").lower(),
            difficulty=difficulty, interviewer_mode=interviewer_mode,
        )
        turn = InterviewTurn.objects.create(
            session=session, turn_index=0, role=InterviewTurn.Role.INTERVIEWER, question=question_text,
        )

        out = InterviewStartOut({
            "message": "Interview session started successfully.",
            "question": question_text, "session_id": session.id, "turn_index": turn.turn_index,
            "context": session.context or {}, "language": session.language, "difficulty": session.difficulty,
            "interviewer_mode": session.interviewer_mode,
        })
        return Response(out.data, status=status.HTTP_201_CREATED)


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=404)

        last_cand = session.turns.filter(role=InterviewTurn.Role.CANDIDATE).order_by("-turn_index").first()
        if not last_cand:
            return Response({"error": "꼬리질문을 생성할 이전 답변이 없습니다."}, status=400)

        rag_info = session.rag_context
        interview_plan_list = rag_info.get("interview_plan", {}).get("interview_plan", [])

        current_stage = "N/A"
        current_objective = "N/A"
        for stage in interview_plan_list:
            if last_cand.question in stage.get("questions", []):
                current_stage = stage.get("stage", "N/A")
                current_objective = stage.get("objective", "N/A")
                break

        rag_bot = RAGInterviewBot(
            company_name=rag_info["company_name"], job_title=rag_info["job_title"],
            container_name=rag_info["container_name"], index_name=rag_info["index_name"],
            interviewer_mode=session.interviewer_mode,
        )

        analysis = last_cand.scores or {}
        followup_q = rag_bot.generate_follow_up_question(
            last_cand.question or "", last_cand.answer or "", analysis,
            current_stage, current_objective
        )

        followups = [followup_q] if followup_q else ["이전 답변에 대해 더 자세히 설명해주시겠습니까?"]

        last = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last.turn_index + 1 if last else 0),
            role=InterviewTurn.Role.INTERVIEWER,
            question="",
            followups=followups,
        )

        out = InterviewNextOut({"session_id": session.id, "turn_index": turn.turn_index, "followups": followups})
        return Response(out.data, status=200)


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewAnswerIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=404)

        rag_info = session.rag_context
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없는 세션입니다. 면접을 다시 시작해주세요."}, status=400)

        rag_bot = RAGInterviewBot(
            company_name=rag_info["company_name"], job_title=rag_info["job_title"],
            container_name=rag_info["container_name"], index_name=rag_info["index_name"],
            interviewer_mode=session.interviewer_mode,
        )

        if not rag_bot.rag_ready:
            return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=500)

        analysis_result = rag_bot.analyze_answer_with_rag(v.get("question", ""), v["answer"])

        if "error" in analysis_result:
            return Response(analysis_result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        last_turn = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session, turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            role=InterviewTurn.Role.CANDIDATE, question=v.get("question", ""), answer=v["answer"],
            scores=analysis_result, feedback=analysis_result.get("feedback", "")
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
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 이미 종료되었습니다."}, status=404)

        session.report_id = f"report-{session.id}"
        session.status = InterviewSession.Status.FINISHED
        from django.utils import timezone
        session.finished_at = timezone.now()
        session.save(update_fields=["report_id", "status", "finished_at"])

        out = InterviewFinishOut({"report_id": session.report_id, "status": session.status})
        return Response(out.data, status=202)


class InterviewReportAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "세션을 찾을 수 없습니다."}, status=404)

        rag_info = session.rag_context
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없는 세션이므로 리포트를 생성할 수 없습니다."}, status=400)

        rag_bot = RAGInterviewBot(
            company_name=rag_info["company_name"], job_title=rag_info["job_title"],
            container_name=rag_info["container_name"], index_name=rag_info["index_name"],
            interviewer_mode=session.interviewer_mode,
            resume_context=session.resume_context
        )

        if not rag_bot.rag_ready:
            return Response({"error": "RAG 시스템이 준비되지 않아 리포트를 생성할 수 없습니다."}, status=500)

        resume_feedback_analysis = rag_bot.analyze_resume_with_rag()

        transcript = []
        turns = session.turns.filter(role=InterviewTurn.Role.CANDIDATE).order_by("turn_index")
        interview_plan_list = rag_info.get("interview_plan", {}).get("interview_plan", [])

        # 면접 계획과 실제 대화를 매칭하여 transcript 구성
        q_idx = 0
        for stage in interview_plan_list:
            for question_in_plan in stage.get("questions", []):
                if q_idx < len(turns):
                    turn = turns[q_idx]
                    transcript.append({
                        "question_id": f"Q{q_idx + 1}",
                        "stage": stage.get("stage", "N/A"),
                        "question": turn.question, "answer": turn.answer, "analysis": turn.scores,
                    })
                    q_idx += 1

        # 만약 plan보다 turn이 더 많다면 (예: 꼬리질문) 추가
        for i, turn in enumerate(turns[q_idx:], start=q_idx):
             transcript.append({
                "question_id": f"Follow-up {i+1}", "stage": "Follow-up",
                "question": turn.question, "answer": turn.answer, "analysis": turn.scores,
            })

        final_report_data = rag_bot.generate_final_report(
            transcript,
            rag_info.get("interview_plan", {}),
            resume_feedback_analysis
        )

        return Response(final_report_data, status=status.HTTP_200_OK)


def interview_coach_view(request):
    """Renders the AI Interview Coach page."""
    return render(request, "api/interview_coach.html")
