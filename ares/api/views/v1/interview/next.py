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

# V1 FSM: stage_idx와 question_idx를 사용하여 진행 상태를 추적
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0, "question_idx": 0, "followup_idx": 0,
    "pending_followups": [], "done": False,
}

# V1 정규화된 계획을 위한 헬퍼 함수
def _get_question_from_normalized_plan(plan: dict | None, stage_idx: int, question_idx: int) -> Optional[Dict[str, str]]:
    """정규화된 V1 스타일 계획 구조에서 질문을 가져옵니다."""
    if not plan: return None

    stages = plan.get("stages", [])
    if not (0 <= stage_idx < len(stages)): return None

    questions = stages[stage_idx].get("questions", [])
    if not (0 <= question_idx < len(questions)): return None

    q_data = questions[question_idx]
    if isinstance(q_data, dict):
        text = q_data.get("text") or q_data.get("question")
        q_id = q_data.get("id", f"{stage_idx+1}-{question_idx+1}")
        if text:
            return {"text": text, "id": q_id}
    return None


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Get Next Question",
        description="""
Fetches the next question in the interview flow.

- It first checks for and returns any buffered follow-up questions.
- If no follow-ups are available, it proceeds to the next main question based on the normalized interview plan (stages/questions).
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
            session = InterviewSession.objects.prefetch_related('turns').get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        plan = (rag_info.get("interview_plans", {})).get("normalized_plan", {})
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        last_turn = session.turns.order_by("-turn_index").first()

        # --- DEBUG LOGGING ---
        print("--- DEBUG: NEXT VIEW ---")
        print(f"[NEXT_DEBUG] FSM at start: {json.dumps(fsm, ensure_ascii=False)}")
        print(f"[NEXT_DEBUG] Pending Followups: {fsm.get('pending_followups')}")
        print("------------------------")
        # --- END DEBUG LOGGING ---

        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "question": None, "done": True}).data)

        pending_followups = fsm.get("pending_followups", [])
        next_question_text: Optional[str] = None
        next_question_id: str = ""

        # 1. Handle pending follow-ups first
        if v.get("include_followups", True) and pending_followups:
            next_question_text = pending_followups.pop(0)
            fsm["pending_followups"] = pending_followups
            
            main_q_turn_label = fsm.get("last_main_question_id", "FU")
            followup_idx = fsm.get("followup_idx", 0) + 1
            next_question_id = f"{main_q_turn_label}-F{followup_idx}"
            fsm["followup_idx"] = followup_idx

        # 2. If no follow-ups, get the next main question from the normalized plan
        else:
            fsm["pending_followups"] = []
            fsm["followup_idx"] = 0
            
            stage_idx = int(fsm.get("stage_idx", 0))
            question_idx = int(fsm.get("question_idx", 0))

            next_q_data = _get_question_from_normalized_plan(plan, stage_idx, question_idx)
            
            if next_q_data:
                fsm["question_idx"] += 1
            else:
                # 다음 stage의 첫 번째 question으로 이동
                stage_idx += 1
                question_idx = 0
                next_q_data = _get_question_from_normalized_plan(plan, stage_idx, 0)
                if next_q_data:
                    fsm["stage_idx"] = stage_idx
                    fsm["question_idx"] = 1
            
            if next_q_data:
                next_question_text = next_q_data["text"]
                next_question_id = next_q_data["id"]
                fsm["last_main_question_id"] = next_question_id

        if not next_question_text:
            fsm["done"] = True
        else:
            # 전환 구문이 있으면 질문 앞에 추가
            if "pending_transition" in fsm:
                transition = fsm.pop("pending_transition")
                if transition and isinstance(transition, str):
                    next_question_text = f"{transition} {next_question_text}"

        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "question": None, "done": True}).data)

        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 1),
            turn_label=next_question_id,
            role=InterviewTurn.Role.INTERVIEWER,
            question=next_question_text,
        )

        out = InterviewNextOut({
            "session_id": str(session.id), "turn_label": turn.turn_label,
            "question": next_question_text, "done": False,
        })
        return Response(out.data, status=status.HTTP_200_OK)
