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


def _safe_analyze_answer(rag_bot, question: str, answer: str, stage: str):
    if hasattr(rag_bot, "analyze_answer_with_rag"):
        try:
            return rag_bot.analyze_answer_with_rag(
                question=question, answer=answer, stage=stage
            )
        except TypeError:
            # 구버전 시그니처 호환
            return rag_bot.analyze_answer_with_rag(
                question=question, answer=answer
            )
    raise AttributeError("RAGInterviewBot has no compatible analysis method.")


def _safe_generate_followups(
    rag_bot,
    original_question: str,
    answer: str,
    analysis: dict,
    stage: str,
    objective: str,
    limit: int = 2,
) -> List[str]:
    """
    rag_bot.generate_follow_up_question 시그니처가 달라도 안전하게 호출.
    """
    if not hasattr(rag_bot, "generate_follow_up_question"):
        return []
    try:
        fu = rag_bot.generate_follow_up_question(
            original_question=original_question,
            answer=answer,
            analysis=analysis,
            stage=stage,
            objective=objective,
            limit=limit,
        )
    except TypeError:
        fu = rag_bot.generate_follow_up_question(
            original_question=original_question,
            answer=answer,
            analysis=analysis,
            stage=stage,
            objective=objective,
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
        plans = rag_info.get("interview_plans", {})
        normalized_plan = plans.get("normalized_plan", {})

        # 가장 최근의 INTERVIEWER 턴을 가져와 turn_label을 안정적으로 확보
        last_interviewer_turn = (
            session.turns.filter(role=InterviewTurn.Role.INTERVIEWER)
            .order_by("-turn_index")
            .first()
        )
        turn_label = last_interviewer_turn.turn_label if last_interviewer_turn else ""

        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        analysis_result = {}
        followups: List[str] = []

        # 정규화된 계획에서 현재 질문의 유형과 정보를 찾습니다.
        current_question_item = None
        if normalized_plan and turn_label:
            # icebreakers 목록 확인
            for item in normalized_plan.get("icebreakers", []):
                if item.get("id") == turn_label:
                    current_question_item = item
                    break
            # stages 목록 확인
            if not current_question_item:
                for stage in normalized_plan.get("stages", []):
                    for item in stage.get("questions", []):
                        if item.get("id") == turn_label:
                            current_question_item = item
                            break
                    if current_question_item:
                        break

        current_question_type = (
            current_question_item.get("question_type", "unknown")
            if current_question_item
            else "unknown"
        )

        # -------------------------
        # 질문 유형에 따른 분기 처리
        # -------------------------
        if current_question_type == "icebreaking":
            # 1) 아이스브레이킹 답변에 대한 간단 분석
            analysis_result = {
                "feedback": "아이스브레이킹 대화가 확인되었습니다. 편안한 분위기에서 면접을 시작하는 것은 좋습니다.",
                "scores": {},
            }

            # 2) 아이스브레이킹 답변 Turn 저장(후보자)
            last_turn = session.turns.order_by("-turn_index").first()
            candidate_turn = InterviewTurn.objects.create(
                session=session,
                turn_index=(last_turn.turn_index + 1 if last_turn else 0),
                turn_label=turn_label,  # 아이스브레이킹 질문의 레이블
                role=InterviewTurn.Role.CANDIDATE,
                question=v.get("question", ""),
                answer=v["answer"],
                scores=analysis_result,
                feedback=(analysis_result or {}).get("feedback", ""),
            )

            # 3) FSM을 이용해 본 면접의 첫 질문 가져오기
            plan_list = _safe_plan_list(normalized_plan)
            stage_idx = int(fsm.get("stage_idx", 0))
            question_idx = int(fsm.get("question_idx", 0))

            # start 단계에서 stage_idx=0, question_idx=0 이 보장된다는 전제
            try:
                current_stage = plan_list[stage_idx]
                questions = current_stage.get("questions", [])
                question_item = questions[question_idx]
                next_question_text = question_item["text"]
                next_question_label = question_item["id"]
            except (IndexError, KeyError, TypeError):
                return Response(
                    {"error": "Failed to get the first main question from the plan."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            transition_phrase = (
                "네, 답변 감사합니다. 긴장이 좀 풀리셨으면 이제 본격적으로 면접을 시작하겠습니다."
            )
            combined_question = f"{transition_phrase} {next_question_text}"

            # 4) 첫 메인 질문(인터뷰어) Turn 생성
            InterviewTurn.objects.create(
                session=session,
                turn_index=candidate_turn.turn_index + 1,
                turn_label=next_question_label,
                role=InterviewTurn.Role.INTERVIEWER,
                question=combined_question,
            )

            # 5) FSM 업데이트
            fsm["question_idx"] = question_idx + 1
            fsm["pending_followups"] = []
            fsm["followup_idx"] = 0
            session.meta = {**(session.meta or {}), "fsm": fsm}
            session.save(update_fields=["meta"])

            # 6) 응답
            return Response(
                {
                    "analysis": analysis_result,
                    "next_question": {
                        "question": combined_question,
                        "turn_label": next_question_label,
                    },
                    "message": "Icebreaking answer processed. Here is the first main question.",
                    "turn_label": candidate_turn.turn_label,
                },
                status=status.HTTP_200_OK,
            )

        # ----- V2 하이브리드 꼬리질문 답변 처리 로직 -----
        if not rag_info:
            return Response(
                {"error": "RAG 컨텍스트가 없는 세션입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1) RAG Bot 초기화 및 계획 로드
        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""),
            job_title=rag_info.get("job_title", ""),
            interviewer_mode=session.interviewer_mode,
            ncs_context=_ensure_ncs_dict(session.context or {}),
            jd_context=session.jd_context or "",
            resume_context=session.resume_context or "",
        )
        # 정규화된 계획을 직접 할당(호환 목적)
        rag_bot.plan = normalized_plan

        # 2) 답변 분석 실행
        plan_list = _safe_plan_list(normalized_plan)
        stage_idx = int(fsm.get("stage_idx", 0))
        current_stage_title = (
            plan_list[stage_idx].get("title", "N/A") if stage_idx < len(plan_list) else "N/A"
        )
        analysis_result = _safe_analyze_answer(
            rag_bot, v.get("question", ""), v["answer"], current_stage_title
        )

        # 3) 하이브리드 꼬리질문 생성
        predicted_followups: List[str] = []
        if current_question_item:
            raw_fus = current_question_item.get("followups", [])
            predicted_followups = [
                fu.get("text")
                for fu in raw_fus
                if isinstance(fu, dict) and fu.get("text")
            ]

        objective = (
            plan_list[stage_idx].get("objective", "N/A")
            if stage_idx < len(plan_list)
            else "N/A"
        )
        realtime_followups = _safe_generate_followups(
            rag_bot=rag_bot,
            original_question=v.get("question", ""),
            answer=v["answer"],
            analysis=analysis_result,
            stage=current_stage_title,
            objective=objective,
            limit=2,  # 실시간 생성 꼬리질문은 2개로 제한
        )

        combined = (predicted_followups or []) + (realtime_followups or [])
        seen: set[str] = set()
        followups = [x for x in combined if isinstance(x, str) and not (x in seen or seen.add(x))]

        # 4) 후보자 답변 Turn 저장
        last_turn = session.turns.order_by("-turn_index").first()
        new_turn_label = turn_label  # 현재 질문(직전 인터뷰어 턴)의 레이블을 그대로 사용
        new_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            turn_label=new_turn_label,
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        # 5) FSM에 꼬리질문 버퍼 적재
        if followups:
            fsm["pending_followups"] = followups[:MAX_FOLLOWUPS_PER_Q]
            fsm["followup_idx"] = 0

        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        return Response(
            {
                "analysis": analysis_result,
                "followups_buffered": fsm.get("pending_followups", []),
                "message": "Answer stored, analysis done, follow-ups buffered.",
                "turn_label": new_turn.turn_label,
            },
            status=status.HTTP_200_OK,
        )
