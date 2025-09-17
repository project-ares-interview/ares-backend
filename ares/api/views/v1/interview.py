# ares/api/views/v1/interview.py
from __future__ import annotations

import os
import traceback
import uuid
from typing import Any, Dict, List, Optional
from uuid import uuid4

from django.shortcuts import render
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from unidecode import unidecode

# Serializers
from ares.api.serializers.v1.interview import (
    InterviewStartIn,
    InterviewStartOut,
    InterviewNextIn,
    InterviewNextOut,
    InterviewAnswerIn,
    InterviewFinishIn,
    InterviewFinishOut,
)

# Services and Utils
from ares.api.services.company_data import find_affiliates_by_keyword
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services.rag.new_azure_rag_llamaindex import AzureBlobRAGSystem

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
    m = {
        "easy": "easy",
        "normal": "normal",
        "hard": "hard",
        "쉬움": "easy",
        "보통": "normal",
        "어려움": "hard",
    }
    return m.get((x or "normal").lower(), "normal")


def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta:
        return ""
    if (q := (meta.get("ncs_query") or "").strip()):
        return q
    role = (meta.get("role") or meta.get("job_title") or "").strip()
    company = (meta.get("company") or meta.get("name") or "").strip()
    return f"{company} {role}".strip()


def _make_ncs_context(meta: dict[str, Any] | None) -> dict[str, Any]:
    q = _ncs_query_from_meta(meta)
    if not q or not search_ncs_hybrid:
        return {"ncs": [], "ncs_query": q}
    try:
        items = search_ncs_hybrid(q) or []
        compact = [
            {"code": it.get("ncs_code"), "title": it.get("title"), "desc": it.get("summary")}
            for it in items
        ]
        compact = [it for it in compact if it.get("title") or it.get("code") or it.get("desc")]
        return {"ncs": compact, "ncs_query": q}
    except Exception as e:
        log.warning(f"[NCS] hybrid search failed ({e})")
        return {"ncs": [], "ncs_query": q}


FALLBACK_QUESTION = (
    "기아의 생산운영/공정기술 관점에서 효율화가 필요하다고 판단한 영역을 한 가지 선정해, "
    "개선 아이디어와 기대 효과(예: 리드타임, 불량률, 설비가동률 지표)를 근거와 함께 설명해 주시겠습니까?"
)

# FSM 기본값/상한
MAX_FOLLOWUPS_PER_Q = 2  # follow-up 상한
DEFAULT_FSM: Dict[str, Any] = {
    "stage_idx": 0,
    "question_idx": 0,
    "followup_idx": 0,
    "pending_followups": [],  # 직전 SubmitAnswer에서 생성/적재
    "done": False,
}


def _extract_first_question_from_plan(interview_plan_data: dict | list | None) -> str | None:
    """
    설계된 인터뷰 플랜에서 첫 질문 1개를 방어적으로 추출한다.
    기대 구조 (예): {"interview_plan": [{"stage": "...", "questions": ["..."]}, ...]}
    """
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
        if isinstance(q0, str) and q0.strip():
            return q0.strip()
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
    q = q_list[question_idx]
    return q if isinstance(q, str) and q.strip() else None


# =========================
# Views
# =========================
class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        keyword = (request.data or {}).get("keyword", "")
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        rid = _reqid()
        try:
            s = InterviewStartIn(data=request.data)
            s.is_valid(raise_exception=True)
            v = s.validated_data

            meta = v.get("meta") or {}
            company_name = (meta.get("company") or "").strip()
            job_title = (meta.get("job_title") or "").strip()
            if not company_name or not job_title:
                return Response(
                    {"error": "meta 정보에 company와 job_title이 모두 필요합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 인덱스/컨테이너 이름 산출
            safe_company_name = unidecode(company_name.lower()).replace(" ", "-")
            index_name = f"{safe_company_name}-report-index"
            container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")

            difficulty = _normalize_difficulty(v.get("difficulty"))
            interviewer_mode = v.get("interviewer_mode", "team_lead")

            # NCS 컨텍스트(없어도 진행)
            ncs_ctx = v.get("ncs_context") or _make_ncs_context(meta)

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
                    ncs_context=ncs_ctx,
                    jd_context=v.get("jd_context", ""),
                    resume_context=v.get("resume_context", ""),
                    research_context=v.get("research_context", ""),
                    sync_on_init=False,  # 인덱스 동기화는 필요 시 관리자 엔드포인트에서 수행
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
            ncs_ctx_safe = to_jsonable(ncs_ctx)
            rag_context_safe = to_jsonable(rag_context_to_save)

            # FSM 초기화
            fsm = dict(DEFAULT_FSM)

            # DB: 세션/첫 턴 생성
            session = InterviewSession.objects.create(
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                jd_context=v.get("jd_context", ""),
                resume_context=v.get("resume_context", ""),
                ncs_query=ncs_ctx_safe.get("ncs_query", ""),
                meta={**meta_safe, "fsm": fsm},
                context=ncs_ctx_safe,
                rag_context=rag_context_safe,
                language=(v.get("language") or "ko").lower(),
                difficulty=difficulty,
                interviewer_mode=interviewer_mode,
            )
            # 첫 메인 질문(플랜 0-0) 출력
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
    """
    - 후보자 답변을 저장
    - 분석 수행 (구조화 + RAG)
    - follow-up 후보 리스트 생성 후 세션 FSM에 '적재만' (커서 이동 없음)
    """
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

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
                ncs_context=session.context or {},
                jd_context=session.jd_context or "",
                resume_context=session.resume_context or "",
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(answer)")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않았습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # 분석
        try:
            analysis_result = rag_bot.analyze_answer_with_rag(v.get("question", ""), v["answer"])
        except Exception:
            log.exception(f"[{rid}] 답변 분석 중 예외")
            return Response({"error": "답변 분석 중 오류가 발생했습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if isinstance(analysis_result, dict) and "error" in analysis_result:
            return Response(analysis_result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 답변 턴 저장
        last_turn = session.turns.order_by("-turn_index").first()
        cand_turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(last_turn.turn_index + 1 if last_turn else 0),
            role=InterviewTurn.Role.CANDIDATE,
            question=v.get("question", ""),
            answer=v["answer"],
            scores=analysis_result,
            feedback=(analysis_result or {}).get("feedback", ""),
        )

        # follow-up 후보 생성 → FSM.pending_followups에 적재만
        plan_list = _safe_plan_list(rag_info)
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        stage_idx = int(fsm.get("stage_idx", 0))
        question_idx = int(fsm.get("question_idx", 0))

        current_stage = plan_list[stage_idx]["stage"] if stage_idx < len(plan_list) and isinstance(plan_list[stage_idx], dict) else "N/A"
        current_objective = plan_list[stage_idx].get("objective", "N/A") if stage_idx < len(plan_list) and isinstance(plan_list[stage_idx], dict) else "N/A"

        followups: List[str] = []
        try:
            # 1개 생성 (원하면 1~3개 생성으로 확대 가능)
            fu = rag_bot.generate_follow_up_question(
                original_question=v.get("question", ""),
                answer=v["answer"],
                analysis=analysis_result,
                stage=current_stage,
                objective=current_objective,
            )
            if isinstance(fu, str) and fu.strip():
                followups.append(fu.strip())
        except Exception:
            log.exception(f"[{rid}] follow-up 생성 중 예외")

        # FSM 업데이트 (적재만, 커서 미이동)
        fsm["pending_followups"] = (fsm.get("pending_followups") or []) + followups
        fsm["followup_idx"] = 0  # 새로 쌓였으니 인덱스 리셋
        meta_update = session.meta or {}
        meta_update["fsm"] = fsm
        session.meta = meta_update
        session.save(update_fields=["meta"])

        return Response(
            {
                "analysis": analysis_result,
                "followups_buffered": followups,
                "message": "Answer stored, analysis done, follow-ups buffered.",
            },
            status=status.HTTP_200_OK,
        )


class InterviewNextQuestionAPIView(APIView):
    """
    - FSM에 기반해 다음 질문을 결정
      1) pending_followups가 남아있고 followup_idx < 상한 → 해당 꼬리질문 반환
      2) 아니면 메인 플랜 다음 문항으로 커서 이동하여 질문 반환
      3) 전부 소진되면 done:true
    """
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        rid = _reqid()
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        session_id = v["session_id"]

        try:
            session = InterviewSession.objects.get(id=session_id, status=InterviewSession.Status.ACTIVE)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        plan_list = _safe_plan_list(rag_info)

        # FSM 로드
        fsm = (session.meta or {}).get("fsm") or dict(DEFAULT_FSM)
        if fsm.get("done"):
            return Response(InterviewNextOut({"session_id": str(session.id), "turn_index": None, "done": True}).data)

        stage_idx = int(fsm.get("stage_idx", 0))
        question_idx = int(fsm.get("question_idx", 0))
        followup_idx = int(fsm.get("followup_idx", 0))
        pending_followups: List[str] = fsm.get("pending_followups") or []

        # 1) pending follow-ups 우선
        if followup_idx < min(len(pending_followups), MAX_FOLLOWUPS_PER_Q):
            fu_q = pending_followups[followup_idx]
            # 인터뷰어 턴 저장
            last = session.turns.order_by("-turn_index").first()
            turn = InterviewTurn.objects.create(
                session=session,
                turn_index=(last.turn_index + 1 if last else 0),
                role=InterviewTurn.Role.INTERVIEWER,
                question=fu_q,
                followups=[],  # 이 턴 자체가 follow-up
            )
            # 커서: followup_idx + 1
            fsm["followup_idx"] = followup_idx + 1
            session.meta = {**(session.meta or {}), "fsm": fsm}
            session.save(update_fields=["meta"])

            out = InterviewNextOut({"session_id": str(session.id), "turn_index": int(turn.turn_index), "followups": [fu_q], "done": False})
            return Response(out.data, status=status.HTTP_200_OK)

        # 2) follow-ups 소진 → 버퍼 비우고 메인 질문으로 넘어감
        fsm["pending_followups"] = []
        fsm["followup_idx"] = 0

        # 다음 메인 질문 커서 계산
        next_question: Optional[str] = None
        # 현재 위치의 다음 문항
        next_question = _get_current_main_question(plan_list, stage_idx, question_idx + 1)
        if next_question is not None:
            # 같은 stage 내에서 다음 question
            fsm["question_idx"] = question_idx + 1
        else:
            # 다음 stage 첫 question
            next_stage_idx = stage_idx + 1
            next_question = _get_current_main_question(plan_list, next_stage_idx, 0)
            if next_question is not None:
                fsm["stage_idx"] = next_stage_idx
                fsm["question_idx"] = 0
            else:
                # 더 이상 메인 질문 없음 → done
                fsm["done"] = True
                session.meta = {**(session.meta or {}), "fsm": fsm}
                session.save(update_fields=["meta"])
                out = InterviewNextOut({"session_id": str(session.id), "turn_index": None, "done": True})
                return Response(out.data, status=status.HTTP_200_OK)

        # 메인 질문 턴 저장
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
            {"session_id": str(session.id), "turn_index": int(turn.turn_index), "followups": [], "done": False}
        )
        return Response(out.data, status=status.HTTP_200_OK)


class InterviewFinishAPIView(APIView):
    """
    - 세션 종료 처리
    - 리포트 즉시 생성 → 세션 meta["final_report"]에 저장, report_id/finished_at 세팅
    """
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

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
                ncs_context=session.context or {},
                jd_context=session.jd_context or "",
            )
        except Exception:
            log.exception(f"[{rid}] RAGInterviewBot 초기화 실패(finish)")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Transcript 구성 (메인 + follow-up 모두 포함)
        turns = session.turns.order_by("turn_index").all()
        plan_list = _safe_plan_list(rag_info)
        stage_cursor = 0
        q_cursor = 0

        transcript: List[Dict[str, Any]] = []
        for t in turns:
            if t.role == InterviewTurn.Role.INTERVIEWER and t.question:
                # 질문 카드 생성 (stage/objective 매핑)
                stage = plan_list[stage_cursor]["stage"] if stage_cursor < len(plan_list) and isinstance(plan_list[stage_cursor], dict) else "N/A"
                objective = plan_list[stage_cursor].get("objective", "N/A") if stage_cursor < len(plan_list) and isinstance(plan_list[stage_cursor], dict) else "N/A"
                transcript.append(
                    {"question_id": f"{stage_cursor + 1}-{q_cursor + 1}", "stage": stage, "objective": objective, "question": t.question}
                )
            elif t.role == InterviewTurn.Role.CANDIDATE:
                # 가장 최근 질문 카드에 답/분석 매핑
                if transcript:
                    transcript[-1]["answer"] = t.answer
                    transcript[-1]["analysis"] = t.scores

                    # 메인 질문이면 q_cursor 증가(대략적 추적)
                    if "-" in transcript[-1]["question_id"]:
                        q_cursor += 1
                        stage_qs = (plan_list[stage_cursor].get("questions", []) if stage_cursor < len(plan_list) else [])
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
            final_report_data = rag_bot.generate_final_report(
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
        # 메타에 저장
        meta_update = session.meta or {}
        meta_update["final_report"] = final_report_data
        session.meta = meta_update
        session.save(update_fields=["report_id", "status", "finished_at", "meta"])

        out = InterviewFinishOut({"report_id": session.report_id, "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class InterviewReportAPIView(APIView):
    """
    - 세션별 리포트 반환
    - 이미 meta["final_report"]가 있으면 그것을 반환
    - 없으면 온디맨드 생성 후 저장
    """
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        rid = _reqid()
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        rag_info = session.rag_context or {}
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없는 세션이므로 리포트를 생성할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 캐시된 리포트 있으면 즉시 반환
        cached = (session.meta or {}).get("final_report")
        if cached:
            return Response(cached, status=status.HTTP_200_OK)

        # 온디맨드 생성 경로
        try:
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get("company_name", ""),
                job_title=rag_info.get("job_title", ""),
                container_name=rag_info.get("container_name", ""),
                index_name=rag_info.get("index_name", ""),
                interviewer_mode=session.interviewer_mode,
                resume_context=session.resume_context,
                ncs_context=session.context or {},
                jd_context=session.jd_context or "",
            )
        except Exception:
            log.exception("[report] RAGInterviewBot 초기화 실패")
            return Response({"error": "RAG 시스템 초기화 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not getattr(rag_bot, "rag_ready", True):
            return Response({"error": "RAG 시스템이 준비되지 않아 리포트를 생성할 수 없습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Transcript 재구성(간단 버전)
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
            final_report_data = rag_bot.generate_final_report(
                transcript, rag_info.get("interview_plan", {}), resume_feedback_analysis
            )
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


# (선택) 관리자용: 인덱스 동기화 트리거
class InterviewAdminSyncIndexAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company = (request.data or {}).get("company") or ""
        container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")
        if not company:
            return Response({"error": "company 필드가 필요합니다. 예: 기아"}, status=status.HTTP_400_BAD_REQUEST)

        safe_company_name = unidecode(company.lower()).replace(" ", "-")
        index_name = f"{safe_company_name}-report-index"

        try:
            rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            rag_system.sync_index(company_name_filter=company)
        except Exception as e:
            log.exception("[admin_sync] 인덱스 동기화 실패")
            return Response({"error": f"동기화 실패: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"message": "동기화 완료", "company": company, "index": index_name}, status=status.HTTP_200_OK)


def interview_coach_view(request):
    """Renders the AI Interview Coach page."""
    return render(request, "api/interview_coach.html")
