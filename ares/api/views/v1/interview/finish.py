# ares/api/views/v1/interview/finish.py
import traceback
from typing import Any, Dict, List

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.serializers.v1.interview import InterviewFinishIn, InterviewFinishOut
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services import resume_service
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)

def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    # Simplified for brevity, assuming it's defined elsewhere
    return ncs_ctx if isinstance(ncs_ctx, dict) else {}

def _safe_plan_list(rag_info: dict | None) -> List[dict]:
    if not isinstance(rag_info, dict): return []
    plan = rag_info.get("interview_plan", {}).get("interview_plan", [])
    return plan if isinstance(plan, list) else []


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Finish an Interview",
        description="""
Marks an interview session as finished and triggers the generation of the final report.

- This endpoint should be called when the candidate has completed all questions.
- The backend compiles the entire interview transcript, generates a comprehensive report, and saves it.
- The response will contain the session ID, which now also serves as the report ID.
""",
        request=InterviewFinishIn,
        responses=InterviewFinishOut
    )
    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        session_id = s.validated_data["session_id"]

        try:
            session = InterviewSession.objects.select_related('user__profile').get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        
        # --- 컨텍스트 로드 (Profile 우선) ---
        jd_context = ""
        resume_context = ""
        research_context = ""
        if hasattr(session.user, 'profile'):
            jd_context = getattr(session.user.profile, 'jd_context', '')
            resume_context = getattr(session.user.profile, 'resume_context', '')
            research_context = getattr(session.user.profile, 'research_context', '')
        # --- 컨텍스트 로드 끝 ---

        rag_bot = RAGInterviewBot(
            session_id=str(session.id),
            company_name=rag_info.get("company_name", ""), job_title=rag_info.get("job_title", ""),
            interviewer_mode=session.interviewer_mode, resume_context=resume_context,
            ncs_context=_ensure_ncs_dict(session.context), jd_context=jd_context,
        )

        turns = session.turns.order_by("turn_index").all()
        transcript = []
        structured_scores = []
        
        all_turns = list(turns)

        # 전체 턴을 순회하며 대화 기록과 점수 재구성 (꼬리질문 누락 방지)
        for i, turn in enumerate(all_turns):
            # 면접관 턴 처리
            if turn.role == InterviewTurn.Role.INTERVIEWER:
                transcript.append({
                    "role": "interviewer",
                    "text": turn.question,
                    "id": turn.turn_label,
                })
                # 다음 턴이 없거나, 다음 턴이 후보자 답변이 아닌 경우 -> [답변 없음]으로 처리
                if (i + 1 >= len(all_turns)) or (all_turns[i + 1].role != InterviewTurn.Role.CANDIDATE):
                    transcript.append({
                        "role": "candidate",
                        "text": "[답변 없음]",
                        "id": turn.turn_label,
                    })
                    structured_scores.append({
                        "question_id": turn.turn_label,
                        "question": turn.question,
                        "answer": "[답변 없음]",
                        "scoring": {"scoring_reason": "평가 불가 (답변 없음)"}
                    })

            # 후보자 턴 처리
            elif turn.role == InterviewTurn.Role.CANDIDATE:
                transcript.append({
                    "role": "candidate",
                    "text": turn.answer,
                    "id": turn.turn_label,
                })
                # 점수 기록이 있으면 추가
                if turn.scores:
                    structured_scores.append(turn.scores)
                # 점수 기록이 없으면 (예: 아이스브레이킹) 기본 정보 추가
                else:
                    structured_scores.append({
                        "question_id": turn.turn_label,
                        "question": turn.question, # 후보자 턴에도 질문이 저장되어 있음
                        "answer": turn.answer,
                        "scoring": {"scoring_reason": "평가 제외 항목입니다."}
                    })

        # --- 이력서 분석 수행 ---
        full_resume_analysis = {}
        try:
            company_meta = {
                "company_name": rag_info.get("company_name", ""),
                "job_title": rag_info.get("job_title", ""),
            }
            full_resume_analysis = resume_service.analyze_all(
                jd_text=jd_context,
                resume_text=resume_context,
                research_text=research_context,
                company_meta=company_meta
            )
        except Exception as e:
            log.error(f"[{session.id}] Failed to perform resume analysis for final report: {e}")
            full_resume_analysis = {"error": f"Resume analysis failed: {e}"}
        # --- 이력서 분석 끝 ---

        interview_plan = rag_info.get("interview_plans", {}).get("raw_v2_plan", {})
        
        full_contexts = {
            "jd_context": jd_context,
            "resume_context": resume_context,
            "research_context": research_context,
        }

        final_report = rag_bot.build_final_report(
            transcript=transcript,
            structured_scores=structured_scores,
            interview_plan=interview_plan,
            full_resume_analysis=full_resume_analysis,
            full_contexts=full_contexts
        )

        session.status = InterviewSession.Status.FINISHED
        session.finished_at = timezone.now()
        session.meta = {**(session.meta or {}), "final_report": final_report}
        session.save(update_fields=["status", "finished_at", "meta"])

        out = InterviewFinishOut({"report_id": str(session.id), "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)
