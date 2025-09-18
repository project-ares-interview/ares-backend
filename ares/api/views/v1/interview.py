# ares/api/views/v1/interview.py
from __future__ import annotations

"""
Interview API Views

# CHANGELOG
- [NCS Normalize] _ensure_ncs_dict 유틸 추가: 입력 ncs_context가 str/None이어도 dict 보장
- [Start] ncs_ctx = _ensure_ncs_dict(v.get("ncs_context")) or _make_ncs_context(meta)
- [Start] DB 저장 시 ncs_query 접근 전에 타입 가드
- [Answer/Finish/Report] rag_bot 초기화 시 ncs_context에 dict 보장 적용
- [Safe Plan] 첫 질문 추출 실패 시 폴백 질문 사용
- [FSM] follow-up → 메인 질문 진행 상태머신 도입 (stage_idx/question_idx/followup_idx)
- [Next 응답] 항상 "question" 필드를 포함하도록 수정(이전 null 문제 해소)
- [PATCH] Follow-up 개수 하드 리미트(생성/버퍼 적재 시 모두) 적용
"""

import json
import os
import uuid
import traceback
from typing import Any, Dict, List, Optional
from uuid import uuid4

from django.shortcuts import render
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from unidecode import unidecode
from drf_spectacular.utils import extend_schema, OpenApiExample

# Serializers
from ares.api.serializers.v1.interview import (
    InterviewStartIn,
    InterviewStartOut,
    InterviewNextIn,
    InterviewNextOut,
    InterviewAnswerIn,
    InterviewAnswerOut,
    InterviewFinishIn,
    InterviewFinishOut,
    InterviewReportOut,
    FindCompaniesRequestSerializer,
    FindCompaniesResponseSerializer,
)

# Services and Utils
from ares.api.services.company_data import find_affiliates_by_keyword
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services.rag.new_azure_rag_llamaindex import AzureBlobRAGSystem
from ares.api.services.followup_soft import make_soft_followup  # Soft FU

# 직렬화 안전 변환기 및 공용 유틸
from ares.api.utils.common_utils import get_logger
from ares.api.utils.state_utils import to_jsonable

# DB Models
from ares.api.models import InterviewSession, InterviewTurn

try:
    from ares.api.utils.search_utils import search_ncs_hybrid
except ImportError:
    search_ncs_hybrid = None  # 선택적 의존 (없어도 동작)

log = get_logger(__name__)


# =========================
# 내부 상수/유틸
# =========================
def _reqid() -> str:
    return uuid4().hex[:8]


def _normalize_difficulty(x: str | None) -> str:
    m = {"easy": "easy", "normal": "normal", "hard": "hard", "쉬움": "easy", "보통": "normal", "어려움": "hard"}
    return m.get((x or "normal").lower(), "normal")


def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta:
        return ""
    if (q := (meta.get("ncs_query") or "").strip()):
        return q
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

def _safe_analyze_answer(rag_bot, question: str, answer: str, stage: str):
    """
    RAGInterviewBot의 구현 차이에 따른 백워드 호환 래퍼.
    analyze_answer_with_rag에 stage 인자를 전달합니다.
    """
    if hasattr(rag_bot, "analyze_answer_with_rag"):
        try:
            # New signature with stage
            return rag_bot.analyze_answer_with_rag(question=question, answer=answer, stage=stage)
        except TypeError:
            # Fallback to old signature
            return rag_bot.analyze_answer_with_rag(question=question, answer=answer)
    
    if hasattr(rag_bot, "analyze_answer"):
        return rag_bot.analyze_answer(question, answer)
    if hasattr(rag_bot, "analyze_answer_rag"):
        return rag_bot.analyze_answer_rag(question, answer)

    raise AttributeError("RAGInterviewBot has no compatible analysis method.")



FALLBACK_QUESTION = (
    "기아의 생산운영/공정기술 관점에서 효율화가 필요하다고 판단한 영역을 한 가지 선정해, "
    "개선 아이디어와 기대 효과(예: 리드타임, 불량률, 설비가동률 지표)를 근거와 함께 설명해 주시겠습니까?"
)

# 꼬리질문 개수 제한(권장 1~3)
MAX_FOLLOWUPS_PER_Q = 3  # Next에서 이미 사용 중(원본 코드) :contentReference[oaicite:2]{index=2}

DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0,
    "question_idx": 0,
    "followup_idx": 0,
    "pending_followups": [],
    "done": False,
}

# -------------------- NCS 정규화 --------------------
def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    if isinstance(ncs_ctx, dict):
        return {
            "ncs_query": ncs_ctx.get("ncs_query", "") if isinstance(ncs_ctx.get("ncs_query", ""), str) else "",
            "ncs": ncs_ctx.get("ncs", []) if isinstance(ncs_ctx.get("ncs", []), list) else [],
        }
    if isinstance(ncs_ctx, str):
        try:
            j = json.loads(ncs_ctx)
            if isinstance(j, dict):
                return {
                    "ncs_query": j.get("ncs_query", "") if isinstance(j.get("ncs_query", ""), str) else "",
                    "ncs": j.get("ncs", []) if isinstance(j.get("ncs", []), list) else [],
                }
        except Exception:
            pass
        return {"ncs_query": ncs_ctx, "ncs": []}
    return {"ncs_query": "", "ncs": []}


def _extract_first_question_from_plan(interview_plan_data: dict | list | None) -> str | None:
    if not interview_plan_data:
        return None

    plan_list = None
    if isinstance(interview_plan_data, dict):
        plan_list = interview_plan_data.get("interview_plan")
    elif isinstance(interview_plan_data, list):
        plan_list = interview_plan_data
    else:
        return None

    if not isinstance(plan_list, list) or not plan_list:
        return None

    first_stage = plan_list[0]
    if not isinstance(first_stage, dict):
        return None

    questions = first_stage.get("questions")
    if isinstance(questions, list) and questions:
        q0 = questions[0]

        # 1) 순수 문자열
        if isinstance(q0, str) and q0.strip():
            s = q0.strip()
            # 1-1) 문자열이지만 JSON처럼 보이면 파싱 시도
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                try:
                    j = json.loads(s)
                    if isinstance(j, dict) and isinstance(j.get("question"), str):
                        return j["question"].strip()
                except Exception:
                    pass
            return s

        # 2) dict 형태
        if isinstance(q0, dict) and isinstance(q0.get("question"), str):
            return q0["question"].strip()

    return None


def _safe_plan_list(rag_info: dict | None) -> List[dict]:
    if not isinstance(rag_info, dict):
        return []
    plan = rag_info.get("interview_plan")
    if isinstance(plan, dict):
        return plan.get("interview_plan", []) or []
    if isinstance(plan, list):
        return plan
    return []


def _get_current_main_question(plan_list: List[dict], stage_idx: int, question_idx: int) -> Optional[str]:
    if stage_idx < 0 or stage_idx >= len(plan_list):
        return None
    stage = plan_list[stage_idx]
    q_list = stage.get("questions", []) or []
    if question_idx < 0 or question_idx >= len(q_list):
        return None
    
    q_raw = q_list[question_idx]
    question_text = None

    if isinstance(q_raw, str) and q_raw.strip():
        s = q_raw.strip()
        if (s.startswith("{") and s.endswith("}")):
            try:
                j = json.loads(s)
                if isinstance(j, dict) and isinstance(j.get("question"), str):
                    question_text = j["question"].strip()
            except Exception:
                pass
        if not question_text:
            question_text = s
    elif isinstance(q_raw, dict) and isinstance(q_raw.get("question"), str):
        question_text = q_raw.get("question", "").strip()

    return question_text if question_text else None


# =========================
# Views
# =========================
class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Find Affiliate Companies",
        request=FindCompaniesRequestSerializer,
        responses=FindCompaniesResponseSerializer,
    )
    def post(self, request, *args, **kwargs):
        keyword = (request.data or {}).get("keyword", "")
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Start a New Interview Session",
        description="Creates a new interview session based on the provided context and returns the first question.",
        request=InterviewStartIn,
        responses=InterviewStartOut,
        examples=[
            OpenApiExample(
                "Success",
                value={
                    "message": "Interview session started successfully.",
                    "question": "Can you tell me about a challenging project you worked on?",
                    "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                    "turn_index": 0,
                    "context": {},
                    "language": "ko",
                    "difficulty": "normal",
                    "interviewer_mode": "team_lead",
                },
                response_only=True,
                status_codes=["201"],
            )
        ],
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
                return Response(
                    {"error": "meta 정보에 company_name과 job_title이 모두 필요합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 인덱스/컨테이너 이름 산출
            safe_company_name = unidecode(company_name.lower()).replace(" ", "-")
            index_name = f"{safe_company_name}-report-index"
            container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")

            difficulty = _normalize_difficulty(v.get("difficulty"))
            interviewer_mode = v.get("interviewer_mode", "team_lead")

            # NCS 컨텍스트 정규화
            ncs_ctx_input = v.get("ncs_context")
            ncs_ctx = _ensure_ncs_dict(ncs_ctx_input) if ncs_ctx_input is not None else _make_ncs_context(meta)

            log.info(f"[{rid}] 🧠 {company_name} 맞춤 면접 계획 설계 (난이도:{difficulty}, 면접관:{interviewer_mode})")
            log.info(f"[{rid}] 🔎 [QUERY_RAW] company={company_name}, job_title={job_title}, index={index_name}")

            # RAG Bot 준비
            try:
                rag_bot = RAGInterviewBot(
                    company_name=company_name,
                    job_title=job_title,
                    container_name=container_name,
                    index_name=index_name,
                    difficulty=difficulty,
                    interviewer_mode=interviewer_mode,
                    ncs_context=ncs_ctx,  # dict 보장
                    jd_context=v.get("jd_context", ""),
                    resume_context=v.get("resume_context", ""),
                    research_context=v.get("research_context", ""),
                    sync_on_init=False,
                )
            except Exception:
                log.exception(f"[{rid}] RAGInterviewBot 초기화 실패")
                return Response(
                    {"error": "RAG 시스템 초기화 실패. Azure/Search 설정을 확인해주세요."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if not getattr(rag_bot, "rag_ready", True):
                return Response(
                    {"error": "RAG 시스템이 준비되지 않았습니다. Azure 설정을 확인해주세요."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # 인터뷰 플랜 설계 (예외 흡수)
            interview_plan_data: dict | None = None
            try:
                interview_plan_data = rag_bot.design_interview_plan() or {}
            except Exception:
                log.exception(f"[{rid}] 인터뷰 플랜 설계 중 예외")
                interview_plan_data = {}

            # 첫 질문 추출 실패 시 폴백 사용
            question_text = _extract_first_question_from_plan(
                interview_plan_data.get("interview_plan") if isinstance(interview_plan_data, dict) else interview_plan_data
            ) or FALLBACK_QUESTION

            if question_text == FALLBACK_QUESTION:
                log.warning(f"[{rid}] 플랜은 생성됐거나 비어있음. 첫 질문 추출 실패 → 폴백 질문 사용")

            log.info(f"[{rid}] ✅ 구조화 면접 계획 수립 완료")
            log.info(f"[{rid}] Interview Plan Data: {interview_plan_data}")

            # 세션 저장용 컨텍스트(핵심만)
            rag_context_to_save = {
                "interview_plan": interview_plan_data or {},
                "company_name": company_name,
                "job_title": job_title,
                "container_name": container_name,
                "index_name": index_name,
            }

            # 직렬화 안전화
            meta_safe = to_jsonable(meta)
            rag_context_safe = to_jsonable(rag_context_to_save)

            # FSM 초기화
            fsm = dict(DEFAULT_FSM)

            # DB: 세션/첫 턴 생성 (ncs_query 접근 가드)
            ncs_query_val = ncs_ctx.get("ncs_query", "") if isinstance(ncs_ctx, dict) else ""
            session = InterviewSession.objects.create(
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                jd_context=v.get("jd_context", ""),
                resume_context=v.get("resume_context", ""),
                ncs_query=ncs_query_val,
                meta={**meta_safe, "fsm": fsm},
                context=to_jsonable(ncs_ctx),  # 저장은 직렬화 안전 변환
                rag_context=rag_context_safe,
                language=(v.get("language") or "ko").lower(),
                difficulty=difficulty,
                interviewer_mode=interviewer_mode,
            )

            turn = InterviewTurn.objects.create(
                session=session,
                turn_index=0,
                role=InterviewTurn.Role.INTERVIEWER,
                question=question_text,
            )

            out_payload = {
                "message": "Interview session started successfully.",
                "question": question_text,
                "session_id": str(session.id),
                "turn_index": int(turn.turn_index),
                "context": session.context or {},
                "language": session.language,
                "difficulty": session.difficulty,
                "interviewer_mode": session.interviewer_mode,
            }
            out = InterviewStartOut(out_payload)
            return Response(out.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            log.error(f"[{rid}] InterviewStart ERROR: {e}\n{traceback.format_exc()}")
            return Response(
                {"error": str(e), "trace": traceback.format_exc()[:2000], "reqid": rid},
                status=status.HTTP_400_BAD_REQUEST,
            )


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Submit an Answer",
        description="Submits a candidate's answer to a question, triggers analysis, and buffers potential follow-up questions.",
        request=InterviewAnswerIn,
        responses=InterviewAnswerOut,
    )
    def post(self, request, *args, **kwargs):
        rid = _reqid()
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
            return Response(
                {"error": "RAG 컨텍스트가 없는 세션입니다. 면접을 다시 시작해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # RAG Bot
        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
                ncs_context=_ensure_ncs_dict(session.context or {}),  # dict 보장
                jd_context=session.jd_context or "",
                resume_context=session.resume_context or "",
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(answer)")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        # Get current stage for analysis
        plan_list = _safe_plan_list(rag_info)
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        stage_idx = int(fsm.get("stage_idx", 0))
        current_stage = (
            plan_list[stage_idx]["stage"]
            if stage_idx < len(plan_list) and isinstance(plan_list[stage_idx], dict)
            else "N/A"
        )

        # 분석
        try:
            analysis_result = _safe_analyze_answer(
                rag_bot,
                v.get("question", "") or "",
                v["answer"],
                current_stage,
            )
        except Exception:
            log.exception(f"[{rid}] 답변 분석 중 예외")
            return Response(
                {"error": "답변 분석 중 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


        # 답변 턴 저장
        last_turn = session.turns.order_by("-turn_index").first()
        InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        # follow-up 후보 생성 → FSM.pending_followups에 적재만
        current_objective = (
            plan_list[stage_idx].get("objective", "N/A")
            if stage_idx < len(plan_list) and isinstance(plan_list[stage_idx], dict)
            else "N/A"
        )

        followups: List[str] = []

        # Only generate new follow-ups if not already in a follow-up sequence
        if fsm.get("followup_idx", 0) == 0:
            # --- Soft FU Logic (초기 단계 한정) ---
            stage_to_turn_type_map = {"아이스브레이킹": "icebreak", "자기소개": "intro:self", "지원 동기": "intro:motivation"}
            soft_fu_turn_type = stage_to_turn_type_map.get(current_stage)

            if soft_fu_turn_type:
                try:
                    log.info(f"[{rid}] 초기 단계 답변 → Soft Follow-up 시도 (단계: {current_stage})")
                    soft_fu_question = make_soft_followup(
                        llm_call_json=rag_bot._chat_json,
                        turn_type=soft_fu_turn_type,
                        origin_question=v.get("question", ""),
                        user_answer=v["answer"],
                        company_name=rag_info.get("company_name", ""),
                        job_title=rag_info.get("job_title", ""),
                        persona_description=rag_bot.persona["persona_description"],
                    )
                    if soft_fu_question:
                        followups.append(soft_fu_question)
                        log.info(f"[{rid}] 생성된 Soft FU: {soft_fu_question}")
                except Exception:
                    log.exception(f"[{rid}] Soft FU 생성 중 예외 (stage: {current_stage})")
            # --- End Soft FU Logic ---

            # 소프트 FU가 없을 때만 RAG 기반 FU 생성
            if not followups:
                try:
                    # [PATCH] 생성 단계에서도 상한 전달 (이중 안전장치)
                    fu_list = rag_bot.generate_follow_up_question(
                        original_question=v.get("question", ""),
                        answer=v["answer"],
                        analysis=analysis_result,
                        stage=current_stage,
                        objective=current_objective,
                        limit=MAX_FOLLOWUPS_PER_Q,  # [PATCH]
                    )
                    if isinstance(fu_list, list):
                        followups.extend(fu_list)
                except Exception:
                    log.exception(f"[{rid}] follow-up 생성 중 예외")

        # FSM 업데이트 (적재만, 커서 미이동)
        # If new followups were generated, they replace any old pending ones.
        # If no new followups were generated (because we were already in a FU sequence),
        # then pending_followups remains as is, and followup_idx is NOT reset.
        if followups:  # Only update if new followups were actually generated
            # [PATCH] 버퍼 적재 시 하드 클립
            fsm["pending_followups"] = followups[:MAX_FOLLOWUPS_PER_Q]
            fsm["followup_idx"] = 0  # Reset index for the new batch

        meta_update = session.meta or {}
        meta_update["fsm"] = fsm
        session.meta = meta_update
        session.save(update_fields=["meta"])

        return Response(
            {
                "analysis": analysis_result,
                "followups_buffered": fsm.get("pending_followups", []),
                "message": "Answer stored, analysis done, follow-ups buffered.",
            },
            status=status.HTTP_200_OK,
        )


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Get Next Question",
        description="FSM(state machine)에 따라 다음 질문(꼬리질문/메인)을 반환합니다.",
        request=InterviewNextIn,
        responses=InterviewNextOut,
    )
    def post(self, request, *args, **kwargs):
        rid = _reqid()
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]
        include_followups = v.get("include_followups", True)

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        plan_list = _safe_plan_list(rag_info)

        # FSM 로드
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        log.info(f"[{rid}] InterviewNext: Initial FSM: {fsm}")

        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "turn_index": None, "question": None, "done": True}).data)

        stage_idx = int(fsm.get("stage_idx", 0))
        question_idx = int(fsm.get("question_idx", 0))
        followup_idx = int(fsm.get("followup_idx", 0))
        pending_followups: List[str] = fsm.get("pending_followups") or []

        log.info(f"[{rid}] InterviewNext: Current FSM state - stage_idx: {stage_idx}, question_idx: {question_idx}, followup_idx: {followup_idx}, pending_followups count: {len(pending_followups)}")

        # 1) pending follow-ups 우선 (요청 시에만)
        if include_followups and followup_idx < min(len(pending_followups), MAX_FOLLOWUPS_PER_Q):
            fu_q = pending_followups[followup_idx]
            last = session.turns.order_by("-turn_index").first()
            turn = InterviewTurn.objects.create(
                session=session,
                turn_index=(last.turn_index + 1 if last else 0),
                role=InterviewTurn.Role.INTERVIEWER,
                question=fu_q,
                followups=[],
            )
            fsm["followup_idx"] = followup_idx + 1
            session.meta = {**(session.meta or {}), "fsm": fsm}
            session.save(update_fields=["meta"])

            out = InterviewNextOut(
                {
                    "session_id": str(session.id),
                    "turn_index": int(turn.turn_index),
                    "question": fu_q,           # ✅ 항상 question 포함
                    "followups": [fu_q],
                    "done": False,
                }
            )
            return Response(out.data, status=status.HTTP_200_OK)

        # 2) follow-ups 소진/스킵 → 버퍼 비우고 메인 질문으로
        log.info(f"[{rid}] InterviewNext: Follow-ups exhausted or skipped. Moving to next main question.")
        fsm["pending_followups"] = []
        fsm["followup_idx"] = 0

        # 다음 메인 질문 커서 계산
        next_question: Optional[str] = _get_current_main_question(plan_list, stage_idx, question_idx + 1)
        if next_question is not None:
            fsm["question_idx"] = question_idx + 1
        else:
            next_stage_idx = stage_idx + 1
            next_question = _get_current_main_question(plan_list, next_stage_idx, 0)
            if next_question is not None:
                fsm["stage_idx"] = next_stage_idx
                fsm["question_idx"] = 0
            else:
                fsm["done"] = True
                session.meta = {**(session.meta or {}), "fsm": fsm}
                session.save(update_fields=["meta"])
                out = InterviewNextOut({"session_id": str(session.id), "turn_index": None, "question": None, "done": True})
                return Response(out.data, status=status.HTTP_200_OK)

        last = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last.turn_index + 1 if last else 0),
            role=InterviewTurn.Role.INTERVIEWER,
            question=next_question or "",
        )

        session.meta = {**(session.meta or {}), "fsm": fsm}
        session.save(update_fields=["meta"])

        out = InterviewNextOut(
            {
                "session_id": str(session.id),
                "turn_index": int(turn.turn_index),
                "question": next_question or "",   # ✅ 항상 question 포함
                "followups": [],
                "done": False,
            }
        )
        return Response(out.data, status=status.HTTP_200_OK)


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Finish an Interview Session",
        description="세션을 종료하고 최종 리포트를 생성하여 저장합니다.",
        request=InterviewFinishIn,
        responses=InterviewFinishOut,
    )
    def post(self, request, *args, **kwargs):
        rid = _reqid()
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 이미 종료되었습니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없어 리포트를 생성할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        # RAG Bot
        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
                resume_context=session.resume_context,
                ncs_context=_ensure_ncs_dict(session.context or {}),  # dict 보장
                jd_context=session.jd_context or "",
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(finish)")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Transcript 구성 (메인 + follow-up 포함)
        turns = session.turns.order_by("turn_index").all()
        plan_list = _safe_plan_list(rag_info)
        stage_cursor = 0
        q_cursor = 0

        transcript: List[Dict[str, Any]] = []
        for t in turns:
            if t.role == InterviewTurn.Role.INTERVIEWER and t.question:
                stage = (
                    plan_list[stage_cursor]["stage"]
                    if stage_cursor < len(plan_list) and isinstance(plan_list[stage_cursor], dict)
                    else "N/A"
                )
                objective = (
                    plan_list[stage_cursor].get("objective", "N/A")
                    if stage_cursor < len(plan_list) and isinstance(plan_list[stage_cursor], dict)
                    else "N/A"
                )
                transcript.append(
                    {"question_id": f"{stage_cursor + 1}-{q_cursor + 1}", "stage": stage, "objective": objective, "question": t.question}
                )
            elif t.role == InterviewTurn.Role.CANDIDATE:
                if transcript:
                    transcript[-1]["answer"] = t.answer
                    transcript[-1]["analysis"] = t.scores
                    if "-" in transcript[-1]["question_id"]:
                        q_cursor += 1
                        stage_qs = plan_list[stage_cursor].get("questions", []) if stage_cursor < len(plan_list) else []
                        if q_cursor >= len(stage_qs):
                            stage_cursor += 1
                            q_cursor = 0

        # 이력서 분석
        try:
            resume_feedback_analysis = rag_bot.analyze_resume_with_rag()
        except Exception:
            log.exception(f"[{rid}] 이력서 분석 중 예외")
            resume_feedback_analysis = {}

        # 최종 리포트 생성
        try:
            final_report_data = rag_bot.generate_detailed_final_report(
                transcript=transcript,
                interview_plan=rag_info.get("interview_plan", {}),
                resume_feedback_analysis=resume_feedback_analysis,
            )
        except Exception:
            log.exception(f"[{rid}] 최종 리포트 생성 중 예외")
            final_report_data = {
                "error": "리포트 생성 중 오류가 발생했습니다.",
                "transcript": transcript,
                "resume_feedback": resume_feedback_analysis,
            }

        session.report_id = f"report-{session.id}"
        session.status = InterviewSession.Status.FINISHED
        session.finished_at = timezone.now()
        meta_update = session.meta or {}
        meta_update["final_report"] = final_report_data
        session.meta = meta_update
        session.save(update_fields=["report_id", "status", "finished_at", "meta"])

        out = InterviewFinishOut({"report_id": session.report_id, "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class InterviewReportAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Get Interview Report",
        description="세션 ID에 대한 최종 리포트를 반환합니다. 캐시가 없으면 온디맨드 생성합니다.",
        responses=InterviewReportOut,
    )
    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        rid = _reqid()
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없는 세션이므로 리포트를 생성할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 캐시된 리포트 즉시 반환
        cached = (session.meta or {}).get("final_report")
        if cached:
            return Response(cached, status=status.HTTP_200_OK)

        # 온디맨드 생성
        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
                resume_context=session.resume_context,
                ncs_context=_ensure_ncs_dict(session.context or {}),  # dict 보장
                jd_context=session.jd_context or "",
            )
        except Exception:
            log.exception("[report] RAGInterviewBot 초기화 실패")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않아 리포트를 생성할 수 없습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        turns = session.turns.order_by("turn_index").all()
        transcript: List[Dict[str, Any]] = []
        last_q_card: Optional[Dict[str, Any]] = None
        for t in turns:
            if t.role == InterviewTurn.Role.INTERVIEWER and t.question:
                last_q_card = {"question_id": f"T{t.turn_index}", "stage": "N/A", "question": t.question}
                transcript.append(last_q_card)
            elif t.role == InterviewTurn.Role.CANDIDATE and last_q_card is not None:
                last_q_card["answer"] = t.answer
                last_q_card["analysis"] = t.scores

        try:
            resume_feedback_analysis = rag_bot.analyze_resume_with_rag()
        except Exception:
            log.exception("[report] 이력서 분석 중 예외")
            resume_feedback_analysis = {}

        try:
            final_report_data = rag_bot.generate_detailed_final_report(transcript, rag_info.get("interview_plan", {}), resume_feedback_analysis)
        except Exception:
            log.exception("[report] 최종 리포트 생성 중 예외")
            final_report_data = {
                "error": "리포트 생성 중 오류가 발생했습니다.",
                "transcript": transcript,
                "resume_feedback": resume_feedback_analysis,
            }

        # 메타에 캐시
        meta_update = session.meta or {}
        meta_update["final_report"] = final_report_data
        session.meta = meta_update
        session.save(update_fields=["meta"])

        return Response(final_report_data, status=status.HTTP_200_OK)


# (관리자) 인덱스 동기화 트리거
class InterviewAdminSyncIndexAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company = (request.data or {}).get("company_name") or ""
        container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")
        if not company:
            return Response({"error": "company_name 필드가 필요합니다. 예: 기아"}, status=status.HTTP_400_BAD_REQUEST)

        safe_company_name = unidecode(company.lower()).replace(" ", "-")
        index_name = f"{safe_company_name}-report-index"

        try:
            rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            rag_system.sync_index(company_name_filter=company)
        except Exception as e:
            log.exception("[admin_sync] 인덱스 동기화 실패")
            return Response({"error": f"동기화 실패: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"message": "동기화 완료", "company_name": company, "index": index_name}, status=status.HTTP_200_OK)


def interview_coach_view(request):
    """Renders the AI Interview Coach page."""
    return render(request, "api/interview_coach.html")
