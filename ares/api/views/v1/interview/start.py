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
FALLBACK_QUESTION = "가벼운 아이스브레이킹으로 시작해볼게요. 최근에 재미있게 본 콘텐츠가 있나요?"
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
        trace_id = _reqid()
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

            log.info(f"[{trace_id}] 🧠 {company_name} 맞춤 면접 계획 설계 (난이도:{difficulty}, 면접관:{interviewer_mode})")

            rag_bot = RAGInterviewBot(
                company_name=company_name, job_title=job_title,
                difficulty=difficulty, interviewer_mode=interviewer_mode,
                ncs_context=ncs_ctx, jd_context=v.get("jd_context", ""),
                resume_context=v.get("resume_context", ""), research_context=v.get("research_context", ""),
            )

            if not getattr(rag_bot, "rag_ready", True):
                return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            plans = rag_bot.design_interview_plan()

            # get_first_question은 RAGInterviewBot 내부의 self.plan (정규화된 계획)을 사용합니다.
            first = rag_bot.get_first_question()

            if not first:
                log.warning(f"[{trace_id}] 첫 질문 추출 실패 → 폴백 질문 사용")
                first = {"id": "FALLBACK-1", "question": FALLBACK_QUESTION}

            log.info(f"[{trace_id}] ✅ 구조화 면접 계획 수립 완료")

            rag_context_to_save = {
                "interview_plans": plans,  # raw_v2_plan과 normalized_plan이 모두 포함된 dict
                "company_name": company_name,
                "job_title": job_title, "container_name": container_name, "index_name": index_name,
            }

            # FSM 초기화: 메인 질문은 0,0 부터 시작
            fsm = dict(DEFAULT_FSM)
            fsm["stage_idx"] = 0
            fsm["question_idx"] = 0

            session = InterviewSession.objects.create(
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                jd_context=v.get("jd_context", ""), resume_context=v.get("resume_context", ""),
                ncs_query=ncs_ctx.get("ncs_query", ""), meta={**to_jsonable(meta), "fsm": fsm},
                context=to_jsonable(ncs_ctx), rag_context=to_jsonable(rag_context_to_save),
                language=(v.get("language") or "ko").lower(), difficulty=difficulty,
                interviewer_mode=interviewer_mode,
            )

            turn = InterviewTurn.objects.create(
                session=session, turn_index=0, turn_label=first.get("id", "1"), role=InterviewTurn.Role.INTERVIEWER, question=first["question"],
            )

            out = InterviewStartOut({
                "message": "Interview session started successfully.", "question": first["question"],
                "session_id": str(session.id), "turn_label": turn.turn_label,
                "context": session.context or {}, "language": session.language,
                "difficulty": session.difficulty, "interviewer_mode": session.interviewer_mode,
            })
            return Response(out.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            log.error(f"[{trace_id}] InterviewStart ERROR: {e}\n{traceback.format_exc()}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
