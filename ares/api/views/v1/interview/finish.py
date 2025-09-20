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
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""), job_title=rag_info.get("job_title", ""),
            container_name=rag_info.get("container_name", ""), index_name=rag_info.get("index_name", ""),
            interviewer_mode=session.interviewer_mode, resume_context=session.resume_context,
            ncs_context=_ensure_ncs_dict(session.context), jd_context=session.jd_context,
        )

        turns = session.turns.order_by("turn_index").all()
        transcript = []
        structured_scores = []
        
        for t in turns:
            role_str = "interviewer" if t.role == InterviewTurn.Role.INTERVIEWER else "candidate"
            text = t.question if t.role == InterviewTurn.Role.INTERVIEWER else t.answer
            
            transcript.append({
                "role": role_str,
                "text": text,
                "id": t.turn_label,
            })
            
            if t.role == InterviewTurn.Role.CANDIDATE and t.scores:
                structured_scores.append(t.scores)

        interview_plan = rag_info.get("interview_plans", {}).get("raw_v2_plan", {})
        final_report = rag_bot.build_final_report(
            transcript=transcript,
            structured_scores=structured_scores,
            interview_plan=interview_plan,
            resume_feedback={}  # 현재 이력서 분석 기능이 없으므로 빈 dict 전달
        )

        session.status = InterviewSession.Status.FINISHED
        session.finished_at = timezone.now()
        session.meta = {**(session.meta or {}), "final_report": final_report}
        session.save(update_fields=["status", "finished_at", "meta"])

        out = InterviewFinishOut({"report_id": str(session.id), "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)
