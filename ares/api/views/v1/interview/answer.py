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
    "current_followup_count": 0,
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

        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)

        # --- 1. 보류 중인 꼬리질문 우선 처리 ---
        pending_followups = fsm.get("pending_followups", [])
        followup_idx = fsm.get("followup_idx", 0)

        if pending_followups and followup_idx < len(pending_followups):
            # 다음 꼬리질문 선택
            next_followup_question = pending_followups[followup_idx]
            
            # 계층적 ID 생성 (예: 2-1-2)
            parent_id = fsm.get("current_main_question_id", "follow-up")
            next_turn_label = f"{parent_id}-{followup_idx + 1}"

            fsm["followup_idx"] += 1
            
            # 마지막 꼬리질문인지 확인 후 상태 초기화
            if fsm["followup_idx"] >= len(pending_followups):
                fsm["pending_followups"] = []
                fsm["followup_idx"] = 0
                # 부모 ID는 다음 주 질문으로 넘어갈 때 초기화되므로 여기서 지우지 않음

            # 현재 답변에 대한 분석은 동일하게 수행
            analysis_result, _, new_turn = self._analyze_and_save_turn(v, session, rag_bot, normalized_plan, fsm)
            
            # 다음 질문으로 꼬리질문 설정
            next_question_obj = {"turn_label": next_turn_label, "question": next_followup_question}
            
            session.meta = {**(session.meta or {}), "fsm": fsm}
            session.save(update_fields=["meta"])

            payload = {
                "analysis": analysis_result,
                "transition_phrase": analysis_result.get("transition_phrase"),
                "next_question": next_question_obj,
                "message": "Processing pending follow-up.",
                "turn_label": new_turn.turn_label,
            }
            return Response(InterviewAnswerOut(payload).data, status=status.HTTP_200_OK)
        
        # --- 2. 새로운 주 질문 처리 ---
        # (꼬리질문이 없을 때만 이 로직 실행)
        fsm["pending_followups"] = []
        fsm["followup_idx"] = 0

        analysis_result, current_question_item, new_turn = self._analyze_and_save_turn(v, session, rag_bot, normalized_plan, fsm)
        
        # 꼬리질문 총량 확인 및 생성
        followup_count = fsm.get("current_followup_count", 0)
        new_followups = []
        if followup_count < MAX_FOLLOWUPS_PER_Q:
            generated, analysis_payload = self._generate_new_followups(v, session, rag_bot, analysis_result, normalized_plan, fsm, current_question_item)
            
            # 자기소개 분석 후, 지원동기 포함 여부 플래그 설정
            if analysis_payload.get("is_combined_intro"):
                fsm["skip_motivation_question"] = analysis_payload.get("motivation_covered", False)

            # 생성된 꼬리질문 개수만큼만 슬라이싱하여 총량을 넘지 않도록 보장
            allowed_count = MAX_FOLLOWUPS_PER_Q - followup_count
            new_followups = generated[:allowed_count]
            fsm["current_followup_count"] = followup_count + len(new_followups)

        # 다음 질문 결정
        next_question_obj = self._determine_next_question(new_followups, normalized_plan, fsm, current_question_item)

        # FSM 상태 저장
        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        payload = {
            "analysis": analysis_result,
            "transition_phrase": analysis_result.get("transition_phrase"),
            "next_question": next_question_obj,
            "message": "Answer processed.",
            "turn_label": new_turn.turn_label,
        }
        return Response(InterviewAnswerOut(payload).data, status=status.HTTP_200_OK)

    def _analyze_and_save_turn(self, validated_data, session, rag_bot, normalized_plan, fsm):
        v = validated_data
        last_interviewer_turn = (
            session.turns.filter(role=InterviewTurn.Role.INTERVIEWER)
            .order_by("-turn_index")
            .first()
        )
        # 꼬리질문 턴인 경우, 부모 ID를 turn_label로 사용
        turn_label = fsm.get("current_main_question_id") or (last_interviewer_turn.turn_label if last_interviewer_turn else "")

        current_question_item = self._get_current_question_item(normalized_plan, turn_label)
        current_question_type = (current_question_item.get("question_type", "unknown") if current_question_item else "unknown")

        if current_question_type == "icebreaking" or (turn_label and "icebreaker" in turn_label):
            analysis_result = {
                "question_id": turn_label,
                "feedback": "아이스브레이킹 대화가 확인되었습니다. 편안한 분위기에서 면접을 시작하는 것은 좋습니다.",
                "scores": {},
            }
        else:
            plan_list = _safe_plan_list(normalized_plan)
            stage_idx = int(fsm.get("stage_idx", 0))
            current_stage_title = (
                plan_list[stage_idx].get("title", "N/A") if stage_idx < len(plan_list) else "N/A"
            )
            analysis_result = _safe_analyze_answer(
                rag_bot, v.get("question", ""), v["answer"], current_stage_title, current_question_item
            )
        
        # 턴 저장
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
        return analysis_result, current_question_item, new_turn

    def _get_current_question_item(self, normalized_plan, turn_label):
        if not normalized_plan or not turn_label:
            return None
        
        # 꼬리질문인 경우 부모 ID로 원본 질문을 찾음
        main_question_id = turn_label.split('-')[0]
        
        for item in normalized_plan.get("icebreakers", []):
            if item.get("id") == main_question_id:
                return item
        for stage in normalized_plan.get("stages", []):
            for item in stage.get("questions", []):
                if item.get("id") == main_question_id:
                    return item
        return None

    def _generate_new_followups(self, v, session, rag_bot, analysis_result, normalized_plan, fsm, current_question_item):
        current_question_type = (current_question_item.get("question_type", "unknown") if current_question_item else "unknown")
        
        # 자기소개/지원동기 전용 꼬리질문 생성기 호출
        if current_question_type in ["self_intro", "motivation"]:
            # 자기소개 답변 시에는 combined 분석을 시도
            turn_type = "intro:combined" if current_question_type == "self_intro" else "intro:motivation"
            
            analysis_payload = {}
            soft_followup, analysis_payload = make_soft_followup(
                llm_call_json=rag_bot.base._chat_json,
                turn_type=turn_type,
                origin_question=v.get("question", ""),
                user_answer=v["answer"],
                company_name=rag_bot.base.company_name,
                job_title=rag_bot.base.job_title,
                persona_description=rag_bot.base.persona.get("persona_description", ""),
                force=True
            )
            return [soft_followup] if soft_followup else [], analysis_payload

        # 그 외 모든 질문에 대한 범용 꼬리질문 생성
        plan_list = _safe_plan_list(normalized_plan)
        stage_idx = int(fsm.get("stage_idx", 0))
        current_stage_title = (
            plan_list[stage_idx].get("title", "N/A") if stage_idx < len(plan_list) else "N/A"
        )
        objective = (
            plan_list[stage_idx].get("objective", "N/A")
            if stage_idx < len(plan_list)
            else "N/A"
        )
        generated = _safe_generate_followups(
            rag_bot=rag_bot,
            original_question=v.get("question", ""),
            answer=v["answer"],
            analysis=analysis_result,
            stage=current_stage_title,
            objective=objective,
            question_item=current_question_item,
            limit=2,
        )
        return generated, {}

    def _determine_next_question(self, new_followups, normalized_plan, fsm, current_question_item):
        # 1. 새로운 꼬리질문이 생성되면, 최우선으로 처리
        if new_followups:
            parent_question_id = current_question_item.get("id", "follow-up-parent")
            fsm["current_main_question_id"] = parent_question_id
            fsm["pending_followups"] = new_followups[:MAX_FOLLOWUPS_PER_Q]
            fsm["followup_idx"] = 1
            
            first_followup_id = f"{parent_question_id}-1"
            return {"turn_label": first_followup_id, "question": new_followups[0]}

        # 2. 꼬리질문이 없으면, 계획된 다음 주 질문으로 이동
        fsm.pop("current_main_question_id", None)
        fsm["current_followup_count"] = 0  # 카운터 초기화
        plan_stages = _safe_plan_list(normalized_plan)
        if not plan_stages:
            fsm["done"] = True
            return None

        stage_idx = fsm.get("stage_idx", 0)
        question_idx = fsm.get("question_idx", 0)

        # 아이스브레이킹 단계에서 넘어온 경우, stage_idx를 0으로 설정
        current_question_type = (current_question_item.get("question_type", "unknown") if current_question_item else "unknown")
        if current_question_type == "icebreaking":
            stage_idx = 0
            question_idx = -1 # 다음 로직에서 +1 되어 0부터 시작
        
        # 다음 질문 찾기
        question_idx += 1
        while stage_idx < len(plan_stages):
            questions_in_stage = [q for q in plan_stages[stage_idx].get("questions", []) if q.get("question_type") != "icebreaking"]
            
            if question_idx < len(questions_in_stage):
                next_q = questions_in_stage[question_idx]
                
                # '지능적 건너뛰기' 로직
                if fsm.get("skip_motivation_question") and next_q.get("question_type") == "motivation":
                    fsm.pop("skip_motivation_question", None) # 플래그 사용 후 제거
                    question_idx += 1 # 다음 질문으로 한번 더 이동
                    continue

                fsm["stage_idx"] = stage_idx
                fsm["question_idx"] = question_idx
                return {"turn_label": next_q.get("id"), "question": next_q.get("question")}
            
            # 다음 스테이지로 이동
            stage_idx += 1
            question_idx = 0
        
        # 모든 면접 질문이 끝남
        fsm["done"] = True
        return None

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
        current_turn_label = last_interviewer_turn.turn_label if last_interviewer_turn else "N/A"
        candidate_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            turn_label=current_turn_label,
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores={"question_id": current_turn_label, "intent": intent, "feedback": "돌발상황으로 분석을 건너뜁니다."},
        )

        # AI의 대응 Turn 저장
        new_turn_index = candidate_turn.turn_index + 1
        exception_turn_label = f"EXCEPTION-{new_turn_index}"
        new_interviewer_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=new_turn_index,
            turn_label=exception_turn_label,
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
