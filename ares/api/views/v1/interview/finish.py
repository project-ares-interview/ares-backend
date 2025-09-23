# ares/api/views/v1/interview/finish.py
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
from ares.api.models.interview_report import InterviewReport

log = get_logger(__name__)


def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    # Simplified for brevity, assuming it's defined elsewhere
    return ncs_ctx if isinstance(ncs_ctx, dict) else {}


def _safe_plan_list(rag_info: dict | None) -> List[dict]:
    if not isinstance(rag_info, dict):
        return []
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
        responses=InterviewFinishOut,
    )
    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        session_id = s.validated_data["session_id"]

        try:
            session = InterviewSession.objects.select_related("user__profile").get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}

        # --- 컨텍스트 로드 (Profile 우선) ---
        jd_context = ""
        resume_context = ""
        research_context = ""
        if hasattr(session.user, "profile"):
            jd_context = getattr(session.user.profile, "jd_context", "")
            resume_context = getattr(session.user.profile, "resume_context", "")
            research_context = getattr(session.user.profile, "research_context", "")
        # --- 컨텍스트 로드 끝 ---

        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""),
            job_title=rag_info.get("job_title", ""),
            interviewer_mode=session.interviewer_mode,
            resume_context=resume_context,
            ncs_context=_ensure_ncs_dict(session.context),
            jd_context=jd_context,
        )

        turns = session.turns.order_by("turn_index").all()
        transcript = []
        structured_scores = []
        
        interviewer_turns = {turn.turn_label: turn for turn in turns if turn.role == InterviewTurn.Role.INTERVIEWER}
        candidate_answers = {turn.turn_label: turn for turn in turns if turn.role == InterviewTurn.Role.CANDIDATE}

        for label, interviewer_turn in interviewer_turns.items():
            candidate_turn = candidate_answers.get(label)
            
            transcript.append({
                "role": "interviewer",
                "text": interviewer_turn.question,
                "id": label,
            })

            if candidate_turn:
                transcript.append({
                    "role": "candidate",
                    "text": candidate_turn.answer,
                    "id": label,
                })
                if candidate_turn.scores:
                    structured_scores.append(candidate_turn.scores)
                else:
                    # 답변은 했지만 분석 결과가 없는 경우 (예: 아이스브레이킹)
                    structured_scores.append({
                        "question_id": label,
                        "question": interviewer_turn.question,
                        "answer": candidate_turn.answer,
                        "scoring": {"scoring_reason": "평가 제외 항목입니다."}
                    })
            else:
                # 후보자가 해당 질문에 답변하지 않은 경우
                transcript.append({
                    "role": "candidate",
                    "text": "[답변 없음]",
                    "id": label,
                })
                structured_scores.append({
                    "question_id": label,
                    "question": interviewer_turn.question,
                    "answer": "[답변 없음]",
                    "scoring": {"scoring_reason": "평가 불가 (답변 없음)"}
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
                company_meta=company_meta,
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

        # --- 세션 상태 업데이트 ---
        session.status = InterviewSession.Status.FINISHED
        session.finished_at = timezone.now()
        session.meta = {**(session.meta or {}), "final_report": final_report}
        session.save(update_fields=["status", "finished_at", "meta"])

        # --- InterviewReport 업서트 저장 ---
        try:
            report_values = {
                "overall_summary": final_report.get("overall_summary", ""),
                "interview_flow_rationale": final_report.get("interview_flow_rationale", ""),
                "strengths_matrix": final_report.get("strengths_matrix", []),
                "weaknesses_matrix": final_report.get("weaknesses_matrix", []),
                "score_aggregation": final_report.get("score_aggregation", {}),
                "missed_opportunities": final_report.get("missed_opportunities", []),
                "potential_followups_global": final_report.get("potential_followups_global", []),
                "resume_feedback": final_report.get("resume_feedback", {}),
                "hiring_recommendation": final_report.get("hiring_recommendation", ""),
                "next_actions": final_report.get("next_actions", []),
                "question_by_question_feedback": final_report.get("question_by_question_feedback", []),
                "tags": final_report.get("tags", []),
                "version": str(final_report.get("version", "")),
            }
            report, created = InterviewReport.objects.update_or_create(
                user=session.user,
                session=session,
                defaults=report_values,
            )
            log.info("[%s] InterviewReport %s: %s", session.id, "created" if created else "updated", report.id)
        except Exception:
            log.exception("[%s] Failed to upsert InterviewReport", session.id)

        out = InterviewFinishOut({"report_id": str(session.id), "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)
