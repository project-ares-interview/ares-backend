# ares/api/views/v1/interview/answer.py
import json
import traceback
from typing import Any, Dict, List

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.serializers.v1.interview import InterviewAnswerIn, InterviewAnswerOut
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services.followup_soft import make_soft_followup
from ares.api.utils.common_utils import get_logger
from ares.api.services.rag.bot.utils import extract_first_main_question

log = get_logger(__name__)

# Constants and Helpers from the original file
MAX_FOLLOWUPS_PER_Q = 3
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0,
    "question_idx": 0,
    "followup_idx": 0,
    "pending_followups": [],
    "done": False,
}


def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    if isinstance(ncs_ctx, dict):
        return {
            "ncs_query": ncs_ctx.get("ncs_query", ""),
            "ncs": ncs_ctx.get("ncs", []),
        }
    if isinstance(ncs_ctx, str):
        try:
            j = json.loads(ncs_ctx)
            if isinstance(j, dict):
                return j
        except Exception:
            pass
        return {"ncs_query": ncs_ctx, "ncs": []}
    return {"ncs_query": "", "ncs": []}


def _safe_plan_list(plan: dict | None) -> List[dict]:
    if not isinstance(plan, dict):
        return []
    stages = plan.get("stages", [])
    return stages if isinstance(stages, list) else []


def _safe_analyze_answer(rag_bot, question: str, answer: str, stage: str, question_item: dict | None = None):
    if hasattr(rag_bot, "analyze_answer_with_rag"):
        try:
            # 최신 시그니처 먼저 시도
            return rag_bot.analyze_answer_with_rag(
                question=question, answer=answer, stage=stage, question_item=question_item
            )
        except TypeError:
            # 구버전 시그니처 호환
            return rag_bot.analyze_answer_with_rag(
                question=question, answer=answer, stage=stage
            )
    raise AttributeError("RAGInterviewBot has no compatible analysis method.")


def _safe_generate_followups(
    rag_bot,
    original_question: str,
    answer: str,
    analysis: dict,
    stage: str,
    objective: str,
    question_item: dict | None = None,
    limit: int = 2,
) -> List[str]:
    """
    rag_bot.generate_follow_up_question 시그니처가 달라도 안전하게 호출.
    """
    if not hasattr(rag_bot, "generate_follow_up_question"):
        return []
    try:
        # 최신 시그니처 먼저 시도
        fu = rag_bot.generate_follow_up_question(
            original_question=original_question,
            answer=answer,
            analysis=analysis,
            stage=stage,
            objective=objective,
            question_item=question_item,
            limit=limit,
        )
    except TypeError:
        # 구버전 시그니처 호환
        fu = rag_bot.generate_follow_up_question(
            original_question=original_question,
            answer=answer,
            analysis=analysis,
            stage=stage,
            objective=objective,
            limit=limit,
        )
    if not isinstance(fu, list):
        return []
    # 문자열만 남기기
    return [x for x in fu if isinstance(x, str) and x.strip()]


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Submit an Answer",
        description="""
Submits a candidate's answer to a given question during an active interview session.

- The backend receives the answer, analyzes it against the interview context (job description, resume, etc.).
- It generates feedback and scores for the answer.
- It may also generate follow-up questions based on the answer and buffer them for the next turn.
""",
        request=InterviewAnswerIn,
        responses=InterviewAnswerOut,
    )
    def post(self, request, *args, **kwargs):
        s = InterviewAnswerIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response(
                {"detail": "유효하지 않은 세션입니다."},
                status=status.HTTP_404_NOT_FOUND,
            )

        rag_info = session.rag_context or {}
        
        # RAG Bot 초기화
        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""),
            job_title=rag_info.get("job_title", ""),
            interviewer_mode=session.interviewer_mode,
            ncs_context=_ensure_ncs_dict(session.context or {}),
            jd_context=getattr(session, 'jd_context', ''),
            resume_context=getattr(session, 'resume_context', ''),
        )

        # 의도 분류
        intent = rag_bot.classify_user_intent(question=v.get("question", ""), answer=v["answer"])
        log.info(f"[{session.id}] Classified intent: {intent}")

        # 의도에 따른 분기 처리
        if intent == "ANSWER":
            # 기존의 상세 분석 로직 실행
            return self.handle_answer(request, v, session, rag_bot)
        else:
            # 돌발상황 처리
            return self.handle_exception_intent(request, v, session, intent)

    def handle_answer(self, request, validated_data, session, rag_bot):
        v = validated_data
        rag_info = session.rag_context or {}
        plans = rag_info.get("interview_plans", {})
        normalized_plan = plans.get("normalized_plan", {})

        last_interviewer_turn = (
            session.turns.filter(role=InterviewTurn.Role.INTERVIEWER)
            .order_by("-turn_index")
            .first()
        )
        turn_label = last_interviewer_turn.turn_label if last_interviewer_turn else ""

        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        
        current_question_item = None
        if normalized_plan and turn_label:
            for item in normalized_plan.get("icebreakers", []):
                if item.get("id") == turn_label:
                    current_question_item = item
                    break
            if not current_question_item:
                for stage in normalized_plan.get("stages", []):
                    for item in stage.get("questions", []):
                        if item.get("id") == turn_label:
                            current_question_item = item
                            break
                    if current_question_item:
                        break
        
        current_question_type = (current_question_item.get("question_type", "unknown") if current_question_item else "unknown")

        # 아이스브레이킹 질문 특별 처리
        if current_question_type == "icebreaking" or (turn_label and "icebreaker" in turn_label):
            analysis_result = {
                "feedback": "아이스브레이킹 대화가 확인되었습니다. 편안한 분위기에서 면접을 시작하는 것은 좋습니다.",
                "scores": {},
            }
            followups: List[str] = []
        else:
            # 일반 질문에 대한 상세 분석
            plan_list = _safe_plan_list(normalized_plan)
            stage_idx = int(fsm.get("stage_idx", 0))
            current_stage_title = (
                plan_list[stage_idx].get("title", "N/A") if stage_idx < len(plan_list) else "N/A"
            )
            analysis_result = _safe_analyze_answer(
                rag_bot, v.get("question", ""), v["answer"], current_stage_title, current_question_item
            )

            objective = (
                plan_list[stage_idx].get("objective", "N/A")
                if stage_idx < len(plan_list)
                else "N/A"
            )
            followups = _safe_generate_followups(
                rag_bot=rag_bot,
                original_question=v.get("question", ""),
                answer=v["answer"],
                analysis=analysis_result,
                stage=current_stage_title,
                objective=objective,
                question_item=current_question_item,
                limit=2,
            )

        # ---- 전환 문구 분리 & 꼬리질문 next_question 구성 ----
        transition_phrase = None
        if isinstance(analysis_result, dict):
            transition_phrase = analysis_result.pop("transition_phrase", None)

        next_question_obj = None
        if followups:
            # 꼬리질문 버퍼링 + 첫 번째 꼬리질문을 next_question으로
            fsm["pending_followups"] = followups[:MAX_FOLLOWUPS_PER_Q]
            fsm["followup_idx"] = 0
            next_question_obj = {"turn_label": "follow-up", "question": followups[0]}
        else:
            # 아이스브레이킹 → 첫 메인 질문으로 자연 전환
            if current_question_type == "icebreaking":
                first_main = extract_first_main_question(normalized_plan) if normalized_plan else None
                if first_main:
                    next_question_obj = {"turn_label": first_main.get("id", "main-1"), "question": first_main.get("question", "")}

        # ---- 턴 저장 ----
        last_turn = session.turns.order_by("-turn_index").first()
        new_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            turn_label=turn_label,
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        # FSM 상태 저장 (전환 문구는 별도 보관하고 싶다면 유지)
        if transition_phrase:
            fsm["pending_transition"] = transition_phrase
        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        # ---- 응답: 새로운 스키마 ----
        payload = {
            "analysis": analysis_result,
            "transition_phrase": transition_phrase,
            "next_question": next_question_obj,
            "message": "Answer processed.",
            "turn_label": new_turn.turn_label,
        }
        return Response(InterviewAnswerOut(payload).data, status=status.HTTP_200_OK)

    def handle_exception_intent(self, request, validated_data, session, intent):
        # 이 메서드는 'ANSWER'가 아닌 다른 모든 인텐트를 처리합니다.
        v = validated_data
        last_interviewer_turn = (
            session.turns.filter(role=InterviewTurn.Role.INTERVIEWER)
            .order_by("-turn_index")
            .first()
        )
        
        def _extract_core_question(q: str) -> str:
            if not q:
                return ""
            # 아주 단순한 핵심 추출: '다.' 문장 경계 기준으로 마지막 구절을 사용
            if "다." in q:
                parts = [p.strip() for p in q.split("다.") if p.strip()]
                if parts:
                    core = parts[-1]
                    # 원래 문장 끝이 '다.'로 끝났다면 복원
                    if not core.endswith("다."):
                        core = core + "다."
                    return core
            return q.strip()

        response_text = ""
        if intent == "CLARIFICATION_REQUEST":
            original_question = last_interviewer_turn.question if last_interviewer_turn else ""
            core_question = _extract_core_question(original_question) or "핵심만 다시 여쭤보겠습니다. 해당 경험에 대해 간단히 설명해 주실 수 있을까요?"
            response_text = f"네, 다시 질문드리겠습니다. {core_question}"
        elif intent == "IRRELEVANT":
            original_question = last_interviewer_turn.question if last_interviewer_turn else "이전 질문"
            response_text = f"알겠습니다. 혹시 제가 드렸던 질문인 '{original_question}'에 대해서도 답변해주실 수 있을까요?"
        elif intent == "QUESTION":
            response_text = "좋은 질문입니다. 그 부분은 면접 마지막에 편하게 이야기 나누겠습니다. 우선은 제가 준비한 질문을 몇 가지 더 드려도 괜찮을까요?"
        elif intent == "CANNOT_ANSWER":
            response_text = "알겠습니다. 그럼 다음 질문으로 넘어가겠습니다."
        else:
            # 기타 의도: 원 질문 리마인드
            original_question = last_interviewer_turn.question if last_interviewer_turn else "이전 질문"
            response_text = f"좋습니다. 이어서 '{original_question}'에 관해 조금만 더 구체적으로 말씀해 주실 수 있을까요?"

        # 돌발상황에 대한 후보자 답변 Turn 저장 (분석 결과는 비움)
        last_turn = session.turns.order_by("-turn_index").first()
        candidate_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            turn_label=last_interviewer_turn.turn_label if last_interviewer_turn else "N/A",
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores={"intent": intent, "feedback": "돌발상황으로 분석을 건너뜁니다."},
        )

        # AI의 대응 Turn 저장
        new_interviewer_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=candidate_turn.turn_index + 1,
            turn_label="EXCEPTION",  # 특수 레이블
            role=InterviewTurn.Role.INTERVIEWER,
            question=response_text,
        )

        # 다음 질문을 바로 이어서 보내주기 위해 응답 포맷을 맞춤
        payload = {
            "analysis": {"intent": intent},
            "transition_phrase": None,  # 전환 문구 없음
            "next_question": {
                "question": response_text,
                "turn_label": new_interviewer_turn.turn_label,
            },
            "message": f"Intent '{intent}' handled.",
            "turn_label": candidate_turn.turn_label,
        }
        return Response(InterviewAnswerOut(payload).data, status=status.HTTP_200_OK)
