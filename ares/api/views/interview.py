# ares/api/views/interview.py
from __future__ import annotations

"""
면접(Interview) API:
- Start : 세션 생성 + 첫 질문 (+ NCS 컨텍스트 주입)
- Next  : 꼬리질문 세트(generate_followups)
- Answer: 답변 저장 + STAR-C 채점/피드백
- Finish: 세션 종료 및 리포트 ID 반환
"""

import logging
import uuid
from typing import List, Dict, Any, Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from ares.api.serializers.v1.interview import (
    InterviewStartIn, InterviewStartOut,
    InterviewNextIn, InterviewNextOut,
    InterviewAnswerIn, InterviewAnswerOut,
    InterviewFinishIn, InterviewFinishOut,
)

# 서비스/유틸
from ares.api.utils.file_utils import join_texts
from ares.api.services import interview_service
try:
    from ares.api.utils.search_utils import search_ncs_hybrid
except Exception:
    search_ncs_hybrid = None

try:
    from ares.api.utils.search_utils import search_ncs_hybrid_semantic
except Exception:
    search_ncs_hybrid_semantic = None

# DB 모델
from ares.api.models import InterviewSession, InterviewTurn

log = logging.getLogger(__name__)


# ===== 내부 유틸 =====
def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta:
        return ""
    # 우선순위: meta['ncs_query'] > role/division/company/skills/kpis
    if (q := (meta.get("ncs_query") or "").strip()):
        return q
    role = (meta.get("role") or "").strip()
    division = (meta.get("division") or "").strip()
    company = (meta.get("company") or "").strip()
    skills = meta.get("skills") or []
    kpis = meta.get("jd_kpis") or []
    parts: List[str] = [p for p in [company, division, role] if p]
    if skills:
        parts.append(", ".join([s for s in skills if s]))
    if kpis:
        parts.append(", ".join([k for k in kpis if k]))
    return ", ".join(parts).strip()


def _normalize_difficulty(x: str | None) -> str:
    m = {
        "easy": "easy", "normal": "normal", "hard": "hard", "medium": "normal",
        "쉬움": "easy", "보통": "normal", "어려움": "hard",
    }
    return m.get((x or "normal").lower(), "normal")


def _make_ncs_context(meta: Dict[str, Any] | None, top_k: int = 5) -> Dict[str, Any]:
    """
    meta를 바탕으로 NCS 검색 → [{"code","title","desc","score"}] 형식 리스트 반환
    semantic 있으면 우선, 없거나 실패하면 hybrid, 그것도 없으면 빈 리스트.
    """
    q = _ncs_query_from_meta(meta)
    if not q:
        return {"ncs": [], "ncs_query": ""}

    items: List[Dict[str, Any]] = []

    # 1) semantic 시도 (함수가 있을 때만)
    if search_ncs_hybrid_semantic:
        try:
            sem = search_ncs_hybrid_semantic(q, top_k=top_k)
            items = sem.get("results", []) if isinstance(sem, dict) else (sem or [])
        except Exception as e:
            log.warning(f"[NCS] semantic failed ({e}), fallback to hybrid")

    # 2) hybrid 폴백 (함수가 있을 때만)
    if not items and search_ncs_hybrid:
        try:
            items = search_ncs_hybrid(q, top_k=top_k) or []
        except Exception as e:
            log.warning(f"[NCS] hybrid failed ({e})")

    # 3) 아무 것도 없으면 빈 컨텍스트
    if not items:
        return {"ncs": [], "ncs_query": q}

    compact = []
    for it in items:
        compact.append({
            "code": it.get("ncs_code") or it.get("code"),
            "title": it.get("title") or it.get("ncs_title"),
            "desc": it.get("summary") or it.get("description"),
            "score": it.get("@search.score") or it.get("score"),
        })
    return {"ncs": compact, "ncs_query": q}


# ===== Views =====
class InterviewStartAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewStartIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        jd_context = v["jd_context"]
        resume_context = v["resume_context"]
        research_context = v.get("research_context", "")
        research_bias = v.get("research_bias", True)
        difficulty = _normalize_difficulty(v.get("difficulty"))
        language = (v.get("language") or "ko").lower()
        meta = v.get("meta", {}) or {}

        # 1) 컨텍스트 구성
        base_context = join_texts(
            f"## [공고/JD]\n{jd_context}".strip(),
            f"## [지원서]\n{resume_context}".strip(),
        )
        full_context = join_texts(
            base_context,
            f"## [지원자 리서치]\n{research_context}".strip(),
        )
        context = full_context if (research_bias and (research_context or "").strip()) else base_context

        # 2) NCS 컨텍스트 주입 (실제 검색)
        ncs_ctx = _make_ncs_context(meta, top_k=5)

        # 3) 첫 질문 생성 (메인 질문)
        try:
            question_text = interview_service.generate_main_question_ondemand(
                context=context,
                prev_questions=[],
                difficulty=difficulty,
                meta=meta,
                ncs_query=ncs_ctx.get("ncs_query", ""),
            )
        except Exception as e:
            log.exception("generate_main_question_ondemand failed: %s", e)
            return Response({"error": "질문 생성 실패"}, status=500)

        # 4) 세션/턴 저장
        session = InterviewSession.objects.create(
            user=request.user if getattr(request.user, "is_authenticated", False) else None,
            jd_context=jd_context,
            resume_context=resume_context,
            ncs_query=ncs_ctx.get("ncs_query", ""),
            meta=meta,
            context={"ncs": ncs_ctx.get("ncs", []), "ncs_query": ncs_ctx.get("ncs_query", "")},
            language=language,
            difficulty=difficulty,
        )
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=0,
            role=InterviewTurn.Role.INTERVIEWER,
            question=question_text,
        )

        out = InterviewStartOut({
            "message": "Interview session started successfully.",
            "question": question_text,
            "session_id": session.id,
            "turn_index": turn.turn_index,
            "context": session.context or {},
            "language": session.language,
            "difficulty": session.difficulty,
        })
        return Response(out.data, status=status.HTTP_201_CREATED)


class InterviewNextQuestionAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewNextIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        difficulty = _normalize_difficulty(v.get("difficulty"))
        language = (v.get("language") or "ko").lower()
        session_id = v.get("session_id")

        # === 세션 기반 (권장) ===
        if session_id:
            try:
                session = InterviewSession.objects.get(
                    id=session_id, status=InterviewSession.Status.ACTIVE
                )
            except InterviewSession.DoesNotExist:
                return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=404)

            # 직전 candidate 답변 추출 (있으면 followups 품질 ↑)
            last_cand = session.turns.filter(
                role=InterviewTurn.Role.CANDIDATE
            ).order_by("-turn_index").first()
            last_answer = last_cand.answer if last_cand else None

            # NCS 컨텍스트
            ncs_list = ((session.context or {}).get("ncs")) or []

            try:
                followups = interview_service.generate_followups(
                    meta=session.meta or {},
                    language=language,
                    difficulty=difficulty,
                    ncs_context=ncs_list,
                    based_on_answer=last_answer,
                    modes=["evidence", "why", "how", "risk"],
                    k=4,
                )
            except Exception as e:
                log.exception("generate_followups failed: %s", e)
                return Response({"error": "꼬리질문 생성 실패"}, status=500)

            last = session.turns.order_by("-turn_index").first()
            turn = InterviewTurn.objects.create(
                session=session,
                turn_index=(0 if not last else last.turn_index + 1),
                role=InterviewTurn.Role.INTERVIEWER,
                question="",            # ⬅️ None 금지: 빈 문자열로 저장
                followups=followups,
            )

            out = InterviewNextOut({
                "session_id": session.id,
                "turn_index": turn.turn_index,
                "followups": followups,
            })
            return Response(out.data, status=200)

        # === 구버전(context) 경로 (호환) ===
        try:
            followups = interview_service.generate_followups(
                meta=v.get("meta", {}) or {},
                language=language,
                difficulty=difficulty,
                ncs_context=[],
                based_on_answer=None,
                modes=["evidence", "why", "how", "risk"],
                k=4,
            )
            return Response(InterviewNextOut({"followups": followups}).data, status=200)
        except Exception as e:
            log.exception("legacy followups failed: %s", e)
            return Response({"error": "꼬리질문 생성 실패"}, status=500)


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewAnswerIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        session_id = v.get("session_id")
        question = v.get("question", "")  # 메인/꼬리질문 텍스트(선택, turn_index 지정 시 생략 가능)
        answer = v["answer"]
        language = (v.get("language") or "ko").lower()

        # === 세션 기반 ===
        if session_id:
            try:
                session = InterviewSession.objects.get(
                    id=session_id, status=InterviewSession.Status.ACTIVE
                )
            except InterviewSession.DoesNotExist:
                return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=404)

            # 요청된 turn_index의 interviewer 질문 복원 (있으면 우선)
            req_turn_idx = v.get("turn_index")
            if req_turn_idx is not None:
                try:
                    qturn = session.turns.get(
                        turn_index=req_turn_idx, role=InterviewTurn.Role.INTERVIEWER
                    )
                    # 메인질문이면 question 필드, 꼬리질문이면 followups 중 선택했을 수 있음
                    question_db = qturn.question or question
                except InterviewTurn.DoesNotExist:
                    question_db = question
            else:
                question_db = question

            last = session.turns.order_by("-turn_index").first()
            next_idx = 0 if not last else last.turn_index + 1

            cand_turn = InterviewTurn.objects.create(
                session=session,
                turn_index=next_idx,
                role=InterviewTurn.Role.CANDIDATE,
                question=question_db,
                answer=answer,
            )

            # STAR-C 평가 (서비스 시그니처 정합 + 결과 매핑)
            scoring: Dict[str, Any] = {
                "overall": None, "S": None, "T": None, "A": None, "R": None, "C": None,
                "feedback": "", "tips": []
            }
            try:
                # 서비스 시그니처: score_answer_starc(q, a, meta=None, ncs_query=None)
                res = interview_service.score_answer_starc(
                    q=question_db or "",
                    a=answer,
                    meta=session.meta or {},
                    ncs_query=session.ncs_query or "",
                )
                if isinstance(res, dict):
                    scores_block = res.get("scores") or {}
                    overall = res.get("weighted_total")

                    # overall 없으면 로컬 계산
                    if overall is None and scores_block:
                        try:
                            S = float(scores_block.get("S", 0))
                            T = float(scores_block.get("T", 0))
                            A = float(scores_block.get("A", 0))
                            R = float(scores_block.get("R", 0))
                            C = float(scores_block.get("C", 0))
                            overall = round(S*1.0 + T*1.0 + A*1.2 + R*1.2 + C*0.8, 2)
                        except Exception:
                            overall = None

                    # comments(dict) → feedback 문자열 합성
                    comments = res.get("comments") or {}
                    if isinstance(comments, dict) and comments:
                        feedback_text = "\n".join(
                            f"{k}: {v}" for k, v in comments.items() if (v or "").strip()
                        )
                    else:
                        feedback_text = ""

                    tips_list = res.get("summary") or []

                    scoring.update({
                        "overall": overall,
                        "S": scores_block.get("S"),
                        "T": scores_block.get("T"),
                        "A": scores_block.get("A"),
                        "R": scores_block.get("R"),
                        "C": scores_block.get("C"),
                        "feedback": feedback_text,
                        "tips": tips_list,
                    })
            except Exception as e:
                log.warning("STAR-C evaluation failed: %s", e)

            # 저장
            cand_turn.scores = {k: v for k, v in scoring.items() if k in ["overall","S","T","A","R","C"] and v is not None}
            cand_turn.feedback = scoring.get("feedback", "")
            cand_turn.save(update_fields=["scores", "feedback"])

            out = InterviewAnswerOut({
                "ok": True,
                "session_id": session.id,
                "turn_index": cand_turn.turn_index,
                "scores": {
                    "overall": scoring.get("overall"),
                    "S": scoring.get("S"),
                    "T": scoring.get("T"),
                    "A": scoring.get("A"),
                    "R": scoring.get("R"),
                    "C": scoring.get("C"),
                },
                "feedback": scoring.get("feedback") or "",
                "tips": scoring.get("tips") or [],
            })
            return Response(out.data, status=200)

        # === 구버전(context) 경로 (호환) ===
        scoring = {
            "overall": None, "S": None, "T": None, "A": None, "R": None, "C": None,
            "feedback": "", "tips": []
        }
        try:
            res = interview_service.score_answer_starc(
                q=v.get("question", "") or "",
                a=answer,
                meta=v.get("meta", {}) or {},
                ncs_query="",  # 구버전은 ncs_query 미사용
            )
            if isinstance(res, dict):
                scores_block = res.get("scores") or {}
                overall = res.get("weighted_total")
                if overall is None and scores_block:
                    try:
                        S = float(scores_block.get("S", 0))
                        T = float(scores_block.get("T", 0))
                        A = float(scores_block.get("A", 0))
                        R = float(scores_block.get("R", 0))
                        C = float(scores_block.get("C", 0))
                        overall = round(S*1.0 + T*1.0 + A*1.2 + R*1.2 + C*0.8, 2)
                    except Exception:
                        overall = None

                comments = res.get("comments") or {}
                if isinstance(comments, dict) and comments:
                    feedback_text = "\n".join(
                        f"{k}: {v}" for k, v in comments.items() if (v or "").strip()
                    )
                else:
                    feedback_text = ""

                tips_list = res.get("summary") or []

                scoring.update({
                    "overall": overall,
                    "S": scores_block.get("S"),
                    "T": scores_block.get("T"),
                    "A": scores_block.get("A"),
                    "R": scores_block.get("R"),
                    "C": scores_block.get("C"),
                    "feedback": feedback_text,
                    "tips": tips_list,
                })
        except Exception as e:
            log.warning("legacy STAR-C evaluation failed: %s", e)

        return Response(InterviewAnswerOut({
            "ok": True,
            "scores": {
                "overall": scoring.get("overall"),
                "S": scoring.get("S"),
                "T": scoring.get("T"),
                "A": scoring.get("A"),
                "R": scoring.get("R"),
                "C": scoring.get("C"),
            },
            "feedback": scoring.get("feedback") or "",
            "tips": scoring.get("tips") or [],
        }).data, status=200)


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        session_id = v.get("session_id")

        # === 세션 기반 ===
        if session_id:
            try:
                session = InterviewSession.objects.get(
                    id=session_id, status=InterviewSession.Status.ACTIVE
                )
            except InterviewSession.DoesNotExist:
                return Response({"detail": "유효하지 않은 세션이거나 이미 종료됨"}, status=404)

            # 리포트 ID 더미 생성(후속: Celery로 실제 생성)
            session.report_id = f"report-{session.id}"
            session.status = InterviewSession.Status.FINISHED
            from django.utils import timezone
            session.finished_at = timezone.now()
            session.save(update_fields=["report_id", "status", "finished_at"])

            return Response(InterviewFinishOut({
                "report_id": session.report_id,
                "status": session.status,
            }).data, status=202)

        # === 구버전(context) 경로 (호환) ===
        report_id = f"rep_{uuid.uuid4().hex[:12]}"
        return Response(InterviewFinishOut({
            "report_id": report_id,
            "status": "queued",
        }).data, status=202)
