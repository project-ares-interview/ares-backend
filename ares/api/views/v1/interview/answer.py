# ares/api/views/v1/interview/answer.py
import json
from typing import Any, Dict, List

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.serializers.v1.interview import InterviewAnswerIn, InterviewAnswerOut
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.orchestrator import run_turn_chain
from ares.api.utils.common_utils import get_logger
from ares.api.services.company_data import get_company_description

log = get_logger(__name__)

# Constants
MAX_FOLLOWUPS_PER_Q = 3
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0, "question_idx": 0, "followup_idx": 0,
    "pending_followups": [], "done": False,
}

def _safe_plan_list(plan: dict | None) -> List[dict]:
    if not isinstance(plan, dict): return []
    # CoP 플래너는 raw_v2_plan을 사용
    raw_plan = plan.get("raw_v2_plan", {})
    if isinstance(raw_plan.get("phases"), list):
        return raw_plan["phases"]
    # 레거시 플래너 호환
    legacy_plan = plan.get("interview_plan", [])
    return legacy_plan if isinstance(legacy_plan, list) else []

class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Submit an Answer (CoP)",
        description="""
Submits a candidate's answer to a given question during an active interview session.
This endpoint uses a Chain-of-Prompts (CoP) orchestrator to process the answer.
- The orchestrator analyzes the answer, generates feedback, scores, coaching, and follow-up questions in a structured pipeline.
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
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        
        # RAG Bot은 LLM 호출 기능만 사용
        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""),
            job_title=rag_info.get("job_title", ""),
            interviewer_mode=session.interviewer_mode,
            jd_context=getattr(session, 'jd_context', ''),
            resume_context=getattr(session, 'resume_context', ''),
        )

        # --- 1. 답변 분석 및 꼬리질문 생성 (Orchestrator 호출) ---
        analysis_payload, current_question_item, new_turn = self._analyze_and_save_turn(v, session, rag_bot)
        
        # --- 2. FSM 상태 업데이트 ---
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        
        # 생성된 꼬리질문을 FSM에 저장
        new_followups = analysis_payload.get("followups", [])
        
        # 꼬리질문 총량 관리
        turn_label = new_turn.turn_label or ""
        is_main_question_turn = "-F" not in turn_label

        if is_main_question_turn:
            # 새로운 메인 질문에 대한 답변이므로 카운터 리셋
            fsm["follow_up_count"] = 0
        else:
            # 꼬리 질문에 대한 답변이므로 카운터 증가
            fsm["follow_up_count"] = fsm.get("follow_up_count", 0) + 1

        follow_up_count = fsm.get("follow_up_count", 0)
        
        if new_followups and follow_up_count < MAX_FOLLOWUPS_PER_Q:
            fsm["pending_followups"] = new_followups
            # 메인 질문 ID는 메인 질문에 답변했을 때만 업데이트
            if is_main_question_turn:
                fsm["last_main_question_id"] = turn_label
        else:
            fsm["pending_followups"] = []

        # FSM 상태 저장
        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        # --- 3. 최종 응답 반환 (다음 질문 없음) ---
        payload = {
            "analysis": analysis_payload.get("analysis", {}),
            "transition_phrase": analysis_payload.get("transition_phrase"),
            "message": "Answer processed. Call /next to get the next question.",
            "turn_label": new_turn.turn_label,
        }
        return Response(payload, status=status.HTTP_200_OK)

    def _analyze_and_save_turn(self, validated_data, session, rag_bot):
        v = validated_data
        rag_info = session.rag_context or {}
        normalized_plan = rag_info.get("interview_plans", {})
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)

        last_interviewer_turn = session.turns.filter(role=InterviewTurn.Role.INTERVIEWER).order_by("-turn_index").first()
        turn_label = last_interviewer_turn.turn_label if last_interviewer_turn else "intro-1"

        current_question_item = self._get_current_question_item(normalized_plan, turn_label) or {}
        current_question_type = current_question_item.get("question_type", "unknown")

        analysis_payload = {}
        # 아이스브레이킹 식별 로직 수정: 'intro-1' 또는 'icebreaker' 타입만 아이스브레이킹으로 간주
        if turn_label == "intro-1" or current_question_type == "icebreaking":
            analysis_payload["analysis"] = {
                "feedback": "아이스브레이킹 대화가 확인되었습니다. 편안한 분위기에서 면접을 시작하는 것은 좋습니다.",
                "scoring": {"framework": "N/A", "scoring_reason": "평가 제외 항목입니다."} 
            }
            analysis_payload["followups"] = []
            analysis_payload["transition_phrase"] = "네, 좋습니다. 그럼 이제 본격적으로 면접을 시작하겠습니다."
        else:
            # Orchestrator 호출
            llm_fn = rag_bot.base._chat_json
            
            # 이전 대화 내용 컨텍스트로 전달
            transcript_context = "\n".join(
                [f"{t.role}: {t.question or t.answer}" for t in session.turns.order_by('turn_index')]
            )
            
            ideal_candidate_profile = get_company_description(rag_bot.base.company_name)
            if "정보 없음" in ideal_candidate_profile:
                ideal_candidate_profile = "(별도 인재상 정보 없음)"

            # FSM과 Plan을 기반으로 현재 phase 결정
            plan_stages = _safe_plan_list(normalized_plan)
            current_phase = "core" # 기본값
            try:
                current_phase = plan_stages[fsm.get("stage_idx", 0)].get("phase", "core")
            except IndexError:
                log.warning(f"Could not determine phase for stage_idx {fsm.get('stage_idx', 0)}. Defaulting to 'core'.")

            # 꼬리질문 프롬프트에 필요한 company_context 생성
            company_context = rag_bot.base.summarize_company_context(f"Summarize key business areas and strategies for {rag_bot.base.company_name}")

            analysis_payload = run_turn_chain(
                llm=llm_fn,
                question=v.get("question", ""),
                user_answer=v["answer"],
                resume_blob=rag_bot.base.resume_context,
                jd_text=rag_bot.base.jd_context,
                company_name=rag_bot.base.company_name,
                company_context=company_context,
                job_title=rag_bot.base.job_title,
                persona=rag_bot.base.persona,
                ideal_candidate_profile=ideal_candidate_profile,
                transcript_context=transcript_context,
                plan_item_meta=current_question_item,
                phase=current_phase, # 계산된 phase 전달
            )
        
        # 턴 저장
        analysis_result = analysis_payload.get("analysis", {})
        analysis_result["question_id"] = turn_label
        
        last_turn = session.turns.order_by("-turn_index").first()
        new_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 1),
            turn_label=turn_label,
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result.get("feedback", "")),
        )
        return analysis_payload, current_question_item, new_turn

    def _get_current_question_item(self, normalized_plan, turn_label):
        if not normalized_plan or not turn_label: return None
        
        main_question_id = turn_label.split('-')[0]
        
        plan_stages = _safe_plan_list(normalized_plan)
        for stage in plan_stages:
            # CoP 플래너는 'items', 레거시는 'questions'
            questions = stage.get("items") or stage.get("questions", [])
            for item in questions:
                # CoP 플래너는 'id', 레거시는 'question_id' 등 다양한 키 사용 가능성
                q_id = item.get("id") or item.get("question_id")
                if q_id == main_question_id:
                    return item
        return None