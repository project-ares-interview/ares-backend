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
    "stage_idx": 0,
    "question_idx": 0,
    "followup_idx": 0,
    "pending_followups": [],
    "done": False,
}

# Helper to get question from the new plan structure (안정성 강화: 경로/키 가변성, 폴백 라벨)
def _get_question_from_plan(
    plan: dict | None, stage_idx: int, question_idx: int
) -> Optional[Dict[str, Any]]:
    if not plan:
        return None

    stages = plan.get("phases", [])
    if not isinstance(stages, list) or not (0 <= stage_idx < len(stages)):
        return None

    questions = stages[stage_idx].get("items", [])
    if not isinstance(questions, list) or not (0 <= question_idx < len(questions)):
        return None

    q = questions[question_idx]
    if not isinstance(q, dict):
        return None

    # question/text 어느 키로 와도 수용
    question_content = q.get("question") or q.get("text")
    # id/turn_label 폴백 + 최종 폴백 라벨 생성
    q_id = q.get("id") or q.get("turn_label") or f"S{stage_idx + 1}Q{question_idx + 1}"

    if question_content:
        return {"question": question_content, "id": q_id}
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
        responses=InterviewNextOut,
    )
    def post(self, request, *args, **kwargs):
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = (
                InterviewSession.objects.prefetch_related("turns")
                .get(id=session_id, status=InterviewSession.Status.ACTIVE)
            )
        except InterviewSession.DoesNotExist:
            return Response(
                {"detail": "유효하지 않은 세션입니다."},
                status=status.HTTP_404_NOT_FOUND,
            )

        rag_info = session.rag_context or {}
        # 플랜 경로 폴백: raw_v2_plan → raw_plan → root (root가 직접 phases를 가질 수 있음)
        plans_root = rag_info.get("interview_plans") or rag_info.get("interview_plan") or {}
        plan = plans_root.get("raw_v2_plan") or plans_root.get("raw_plan") or plans_root

        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        last_turn = session.turns.order_by("-turn_index").first()

        if fsm.get("done"):
            out = InterviewNextOut(
                {
                    "session_id": str(session.id),
                    "turn_label": None,
                    "question": None,
                    "question_ssml": None,
                    "followups": [],
                    "done": True,
                }
            )
            return Response(out.data, status=status.HTTP_200_OK)

        pending_followups = fsm.get("pending_followups", [])
        next_question_text: Optional[str] = None
        next_question_ssml: Optional[str] = None
        next_question_id: Optional[str] = None

        # 1) 대기 중인 꼬리질문 우선 소진
        if v.get("include_followups", True) and pending_followups:
            followup_item = pending_followups.pop(0)
            fsm["pending_followups"] = pending_followups

            if isinstance(followup_item, dict):
                next_question_text = followup_item.get("text")
                next_question_ssml = followup_item.get("ssml")
            else: # 레거시 호환
                next_question_text = str(followup_item)
                next_question_ssml = f"<speak>{next_question_text}</speak>"

            main_q_turn_label = fsm.get("last_main_question_id", "FU")
            followup_idx = int(fsm.get("followup_idx", 0)) + 1
            next_question_id = f"{main_q_turn_label}-F{followup_idx}"
            fsm["followup_idx"] = followup_idx

        # 2) 없으면 메인 질문 포인터 전진 (단순 전이: 현재 → 없으면 다음 단계 첫 번째)
        else:
            fsm["pending_followups"] = []
            fsm["followup_idx"] = 0

            stage_idx = int(fsm.get("stage_idx", 0))
            question_idx = int(fsm.get("question_idx", 0))

            next_q_data = _get_question_from_plan(plan, stage_idx, question_idx)

            if not next_q_data:
                # 현재 단계 끝 → 다음 단계의 첫 질문 시도
                stage_idx += 1
                question_idx = 0
                next_q_data = _get_question_from_plan(plan, stage_idx, question_idx)

            if next_q_data:
                question_content = next_q_data.get("question")
                next_question_id = next_q_data.get("id")
                
                if isinstance(question_content, dict):
                    next_question_text = question_content.get("text")
                    next_question_ssml = question_content.get("ssml")
                elif isinstance(question_content, str):
                    next_question_text = question_content
                    next_question_ssml = f"<speak>{question_content}</speak>"

                if next_question_text:
                    fsm["last_main_question_id"] = next_question_id
                    # 다음 호출을 위해 포인터 한 칸 전진
                    fsm["stage_idx"] = stage_idx
                    fsm["question_idx"] = question_idx + 1
                else:
                    next_q_data = None # 유효한 질문 텍스트가 없으면 실패 처리
            
            if not next_q_data:
                log.warning(
                    "No next main question. stages=%s stage_idx=%s question_idx=%s",
                    len(plan.get("phases", []))
                    if isinstance(plan.get("phases", []), list)
                    else "N/A",
                    fsm.get("stage_idx"),
                    fsm.get("question_idx"),
                )
                fsm["done"] = True

        # FSM 저장
        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        if fsm.get("done"):
            out = InterviewNextOut(
                {
                    "session_id": str(session.id),
                    "turn_label": None,
                    "question": None,
                    "question_ssml": None,
                    "followups": [],
                    "done": True,
                }
            )
            return Response(out.data, status=status.HTTP_200_OK)

        # 질문 생성(턴 기록)
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 1),
            turn_label=next_question_id,
            role=InterviewTurn.Role.INTERVIEWER,
            question=next_question_text,
            question_ssml=next_question_ssml,
        )

        out = InterviewNextOut(
            {
                "session_id": str(session.id),
                "turn_label": turn.turn_label,
                "question": next_question_text,
                "question_ssml": next_question_ssml,
                "followups": [],  # next API는 ‘다음 질문’만 반환하므로 빈 리스트로 일관성 유지
                "done": False,
            }
        )
        return Response(out.data, status=status.HTTP_200_OK)
