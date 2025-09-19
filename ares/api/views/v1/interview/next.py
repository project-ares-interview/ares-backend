# ares/api/views/v1/interview/next.py
from typing import Any, Dict, List, Optional

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.serializers.v1.interview import InterviewNextIn, InterviewNextOut
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)

# Constants and Helpers from the original file
MAX_FOLLOWUPS_PER_Q = 3
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0, "question_idx": 0, "followup_idx": 0,
    "pending_followups": [], "done": False,
}

def _safe_plan_list(rag_info: dict | None) -> List[dict]:
    if not isinstance(rag_info, dict): return []
    plan = rag_info.get("interview_plan", {}).get("interview_plan", [])
    return plan if isinstance(plan, list) else []

def _get_current_main_question(plan_list: List[dict], stage_idx: int, question_idx: int) -> Optional[str]:
    if not (0 <= stage_idx < len(plan_list)): return None
    stage = plan_list[stage_idx]
    q_list = stage.get("questions", [])
    if not (0 <= question_idx < len(q_list)): return None
    q_raw = q_list[question_idx]
    return q_raw.get("question", q_raw) if isinstance(q_raw, dict) else str(q_raw)


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Get Next Question",
        description="""
Fetches the next question in the interview flow.

- It first checks for and returns any buffered follow-up questions.
- If no follow-ups are available, it proceeds to the next main question based on the interview plan.
- If no more questions are left, it marks the interview session as done.
""",
        request=InterviewNextIn,
        responses=InterviewNextOut
    )
    def post(self, request, *args, **kwargs):
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        plan_list = _safe_plan_list(rag_info)
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)

        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "question": None, "done": True}).data)

        followup_idx = int(fsm.get("followup_idx", 0))
        pending_followups = fsm.get("pending_followups", [])

        if v.get("include_followups", True) and followup_idx < len(pending_followups):
            next_question = pending_followups[followup_idx]
            fsm["followup_idx"] += 1
        else:
            fsm["pending_followups"] = []
            fsm["followup_idx"] = 0
            stage_idx = int(fsm.get("stage_idx", 0))
            question_idx = int(fsm.get("question_idx", 0))
            
            next_question = _get_current_main_question(plan_list, stage_idx, question_idx + 1)
            if next_question:
                fsm["question_idx"] += 1
            else:
                next_question = _get_current_main_question(plan_list, stage_idx + 1, 0)
                if next_question:
                    fsm["stage_idx"] += 1
                    fsm["question_idx"] = 0
                else:
                    fsm["done"] = True

        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "question": None, "done": True}).data)

        last_turn = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            role=InterviewTurn.Role.INTERVIEWER,
            question=next_question,
        )

        out = InterviewNextOut({
            "session_id": str(session.id), "turn_index": int(turn.turn_index),
            "question": next_question, "done": False,
        })
        return Response(out.data, status=status.HTTP_200_OK)
