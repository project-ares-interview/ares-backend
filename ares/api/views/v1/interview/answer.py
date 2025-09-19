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

log = get_logger(__name__)

# Constants and Helpers from the original file
MAX_FOLLOWUPS_PER_Q = 3
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0, "question_idx": 0, "followup_idx": 0,
    "main_question_index": 0,
    "pending_followups": [], "done": False,
}

def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    if isinstance(ncs_ctx, dict):
        return {"ncs_query": ncs_ctx.get("ncs_query", ""), "ncs": ncs_ctx.get("ncs", [])}
    if isinstance(ncs_ctx, str):
        try:
            j = json.loads(ncs_ctx)
            if isinstance(j, dict): return j
        except Exception: pass
        return {"ncs_query": ncs_ctx, "ncs": []}
    return {"ncs_query": "", "ncs": []}

def _safe_plan_list(rag_info: dict | None) -> List[dict]:
    if not isinstance(rag_info, dict): return []
    plan = rag_info.get("interview_plan", {}).get("interview_plan", [])
    return plan if isinstance(plan, list) else []

def _safe_analyze_answer(rag_bot, question: str, answer: str, stage: str):
    if hasattr(rag_bot, "analyze_answer_with_rag"):
        try:
            return rag_bot.analyze_answer_with_rag(question=question, answer=answer, stage=stage)
        except TypeError:
            return rag_bot.analyze_answer_with_rag(question=question, answer=answer)
    raise AttributeError("RAGInterviewBot has no compatible analysis method.")


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
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없는 세션입니다."}, status=status.HTTP_400_BAD_REQUEST)

        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""), job_title=rag_info.get("job_title", ""),
            container_name=rag_info.get("container_name", ""), index_name=rag_info.get("index_name", ""),
            interviewer_mode=session.interviewer_mode, ncs_context=_ensure_ncs_dict(session.context or {}),
            jd_context=session.jd_context or "", resume_context=session.resume_context or "",
        )

        plan_list = _safe_plan_list(rag_info)
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        stage_idx = int(fsm.get("stage_idx", 0))
        current_stage = plan_list[stage_idx]["stage"] if stage_idx < len(plan_list) else "N/A"

        analysis_result = {}
        followups = []

        if current_stage == "아이스브레이킹":
            analysis_result = {
                "feedback": "아이스브레이킹 질문에 대한 답변이 확인되었습니다.",
                "scores": {}
            }
            # AI를 통해 답변에 대한 자연스러운 전환 멘트 생성
            soft_fu = make_soft_followup(
                llm_call_json=rag_bot._chat_json,
                turn_type="icebreak",
                origin_question=v.get("question", ""),
                user_answer=v["answer"],
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                persona_description=rag_bot.persona["persona_description"],
            )
            if soft_fu:
                followups.append(soft_fu)
        else:
            analysis_result = _safe_analyze_answer(rag_bot, v.get("question", ""), v["answer"], current_stage)
            if fsm.get("followup_idx", 0) == 0:
                stage_map = {"자기소개": "intro:self", "지원 동기": "intro:motivation"}
                if current_stage in stage_map:
                    soft_fu = make_soft_followup(
                        llm_call_json=rag_bot._chat_json, turn_type=stage_map[current_stage],
                        origin_question=v.get("question", ""), user_answer=v["answer"],
                        company_name=rag_info.get("company_name", ""), job_title=rag_info.get("job_title", ""),
                        persona_description=rag_bot.persona["persona_description"],
                    )
                    if soft_fu:
                        followups.append(soft_fu)

                if not followups:
                    objective = plan_list[stage_idx].get("objective", "N/A") if stage_idx < len(plan_list) else "N/A"
                    fu_list = rag_bot.generate_follow_up_question(
                        original_question=v.get("question", ""), answer=v["answer"],
                        analysis=analysis_result, stage=current_stage, objective=objective,
                        limit=MAX_FOLLOWUPS_PER_Q,
                    )
                    if isinstance(fu_list, list):
                        followups.extend(fu_list)

        last_turn = session.turns.order_by("-turn_index").first()
        question_turn = session.turns.filter(question=v.get("question", "")).order_by("-turn_index").first()
        turn_label = question_turn.turn_label if question_turn else "0"

        new_turn = InterviewTurn.objects.create(
            session=session, turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            turn_label=turn_label,
            role=InterviewTurn.Role.CANDIDATE, question=v.get("question", ""),
            answer=v["answer"], scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        if followups:
            fsm["pending_followups"] = followups[:MAX_FOLLOWUPS_PER_Q]
            fsm["followup_idx"] = 0

        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        return Response({
            "analysis": analysis_result,
            "followups_buffered": fsm.get("pending_followups", []),
            "message": "Answer stored, analysis done, follow-ups buffered.",
            "turn_label": new_turn.turn_label,
        }, status=status.HTTP_200_OK)
