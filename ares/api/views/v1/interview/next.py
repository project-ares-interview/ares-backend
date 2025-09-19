# ares/api/views/v1/interview/next.py
import json
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
    "main_question_index": 0,
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
    
    # Case 1: It's a dictionary like {"question": "..."}
    if isinstance(q_raw, dict):
        return q_raw.get("question", "").strip() or None
    # Case 2: It's a string that might be a plain question or a JSON string
    if isinstance(q_raw, str):
        s = q_raw.strip()
        try:
            data = json.loads(s)
            if isinstance(data, dict) and "question" in data:
                return str(data["question"]).strip()
        except json.JSONDecodeError:
            pass  # Not a JSON string, so treat as plain text below
        return s if s else None
    return None


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
        main_question_index = int(fsm.get("main_question_index", 0))
        next_question = None
        turn_label = ""

        if v.get("include_followups", True) and followup_idx < len(pending_followups):
            next_question = pending_followups[followup_idx]
            turn_label = f"{main_question_index}-{followup_idx + 1}"
            fsm["followup_idx"] += 1
        else:
            fsm["pending_followups"] = []
            fsm["followup_idx"] = 0
            stage_idx = int(fsm.get("stage_idx", 0))
            question_idx = int(fsm.get("question_idx", 0))
            
            next_question = _get_current_main_question(plan_list, stage_idx, question_idx + 1)
            if next_question:
                fsm["question_idx"] += 1
                main_question_index += 1
                fsm["main_question_index"] = main_question_index
                turn_label = str(main_question_index)
            else:
                next_question = _get_current_main_question(plan_list, stage_idx + 1, 0)
                if next_question:
                    fsm["stage_idx"] += 1
                    fsm["question_idx"] = 0
                    main_question_index += 1
                    fsm["main_question_index"] = main_question_index
                    turn_label = str(main_question_index)
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
            turn_label=turn_label,
            role=InterviewTurn.Role.INTERVIEWER,
            question=next_question,
        )

        out = InterviewNextOut({
            "session_id": str(session.id), "turn_label": turn.turn_label,
            "question": next_question, "done": False,
        })
        return Response(out.data, status=status.HTTP_200_OK)
