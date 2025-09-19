# ares/api/views/v1/interview/start.py
import json
import os
import traceback
from typing import Any, Dict
from uuid import uuid4

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from unidecode import unidecode
from drf_spectacular.utils import extend_schema, OpenApiExample

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.serializers.v1.interview import InterviewStartIn, InterviewStartOut
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.utils.common_utils import get_logger
from ares.api.utils.state_utils import to_jsonable

try:
    from ares.api.utils.search_utils import search_ncs_hybrid
except ImportError:
    search_ncs_hybrid = None

log = get_logger(__name__)

# Constants from the original file
FALLBACK_QUESTION = (
    "기아의 생산운영/공정기술 관점에서 효율화가 필요하다고 판단한 영역을 한 가지 선정해, "
    "개선 아이디어와 기대 효과(예: 리드타임, 불량률, 설비가동률 지표)를 근거와 함께 설명해 주시겠습니까?"
)
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0, "question_idx": 0, "followup_idx": 0,
    "pending_followups": [], "done": False,
}

# Helper functions from the original file
def _reqid() -> str:
    return uuid4().hex[:8]

def _normalize_difficulty(x: str | None) -> str:
    m = {"easy": "easy", "normal": "normal", "hard": "hard", "쉬움": "easy", "보통": "normal", "어려움": "hard"}
    return m.get((x or "normal").lower(), "normal")

def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta: return ""
    if (q := (meta.get("ncs_query") or "").strip()): return q
    role = (meta.get("role") or meta.get("job_title") or "").strip()
    company = (meta.get("company_name") or meta.get("person_name") or "").strip()
    return f"{company} {role}".strip()

def _make_ncs_context(meta: dict[str, Any] | None) -> dict[str, Any]:
    q = _ncs_query_from_meta(meta)
    if not q or not search_ncs_hybrid:
        return {"ncs": [], "ncs_query": q}
    try:
        items = search_ncs_hybrid(q) or []
        compact = [{"code": it.get("ncs_code"), "title": it.get("title"), "desc": it.get("summary")} for it in items]
        compact = [it for it in compact if it.get("title") or it.get("code") or it.get("desc")]
        return {"ncs": compact, "ncs_query": q}
    except Exception as e:
        log.warning(f"[NCS] hybrid search failed ({e})")
        return {"ncs": [], "ncs_query": q}

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

def _extract_first_question_from_plan(interview_plan_data: dict | list | None) -> str | None:
    if not interview_plan_data: return None
    plan_list = interview_plan_data.get("interview_plan") if isinstance(interview_plan_data, dict) else interview_plan_data
    if not isinstance(plan_list, list) or not plan_list: return None
    first_stage = plan_list[0]
    if not isinstance(first_stage, dict): return None
    questions = first_stage.get("questions")
    if isinstance(questions, list) and questions:
        q0 = questions[0]
        if isinstance(q0, str) and q0.strip():
            s = q0.strip()
            if (s.startswith("{based on the provided string, there are no incorrect escaping issues. The string is already valid Python code and does not contain any problematic escape sequences like \n or \t that need correction. The JSON output reflects this by returning the original string as is.}:") and s.endswith("}")):
                try:
                    j = json.loads(s)
                    if isinstance(j, dict) and isinstance(j.get("question"), str): return j["question"].strip()
                except Exception: pass
            return s
        if isinstance(q0, dict) and isinstance(q0.get("question"), str):
            return q0["question"].strip()
    return None


class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Start an Interview",
        description="""
Initializes a new interview session based on the provided context (JD, resume, etc.).
It generates the first question and returns a new session ID.
""",
        request=InterviewStartIn,
        responses=InterviewStartOut,
    )
    def post(self, request, *args, **kwargs):
        rid = _reqid()
        try:
            s = InterviewStartIn(data=request.data)
            s.is_valid(raise_exception=True)
            v = s.validated_data

            meta = v.get("meta") or {}
            company_name = (meta.get("company_name") or "").strip()
            job_title = (meta.get("job_title") or "").strip()
            if not company_name or not job_title:
                return Response({"error": "meta 정보에 company_name과 job_title이 모두 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

            safe_company_name = unidecode(company_name.lower()).replace(" ", "-")
            index_name = f"{safe_company_name}-report-index"
            container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")
            difficulty = _normalize_difficulty(v.get("difficulty"))
            interviewer_mode = v.get("interviewer_mode", "team_lead")
            ncs_ctx = _ensure_ncs_dict(v.get("ncs_context")) or _make_ncs_context(meta)

            log.info(f"[{rid}] 🧠 {company_name} 맞춤 면접 계획 설계 (난이도:{difficulty}, 면접관:{interviewer_mode})")

            rag_bot = RAGInterviewBot(
                company_name=company_name, job_title=job_title, container_name=container_name,
                index_name=index_name, difficulty=difficulty, interviewer_mode=interviewer_mode,
                ncs_context=ncs_ctx, jd_context=v.get("jd_context", ""),
                resume_context=v.get("resume_context", ""), research_context=v.get("research_context", ""),
                sync_on_init=False,
            )

            if not getattr(rag_bot, "rag_ready", True):
                return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            interview_plan_data = rag_bot.design_interview_plan() or {}
            question_text = _extract_first_question_from_plan(interview_plan_data) or FALLBACK_QUESTION
            if question_text == FALLBACK_QUESTION:
                log.warning(f"[{rid}] 첫 질문 추출 실패 → 폴백 질문 사용")

            log.info(f"[{rid}] ✅ 구조화 면접 계획 수립 완료")

            rag_context_to_save = {
                "interview_plan": interview_plan_data, "company_name": company_name,
                "job_title": job_title, "container_name": container_name, "index_name": index_name,
            }

            session = InterviewSession.objects.create(
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                jd_context=v.get("jd_context", ""), resume_context=v.get("resume_context", ""),
                ncs_query=ncs_ctx.get("ncs_query", ""), meta={**to_jsonable(meta), "fsm": dict(DEFAULT_FSM)},
                context=to_jsonable(ncs_ctx), rag_context=to_jsonable(rag_context_to_save),
                language=(v.get("language") or "ko").lower(), difficulty=difficulty,
                interviewer_mode=interviewer_mode,
            )

            turn = InterviewTurn.objects.create(
                session=session, turn_index=0, role=InterviewTurn.Role.INTERVIEWER, question=question_text,
            )

            out = InterviewStartOut({
                "message": "Interview session started successfully.", "question": question_text,
                "session_id": str(session.id), "turn_index": int(turn.turn_index),
                "context": session.context or {}, "language": session.language,
                "difficulty": session.difficulty, "interviewer_mode": session.interviewer_mode,
            })
            return Response(out.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            log.error(f"[{rid}] InterviewStart ERROR: {e}\n{traceback.format_exc()}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
