from __future__ import annotations
import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from ares.api.services.company_data import (
    find_affiliates_by_keyword,
    get_company_description,
)
from ares.api.services.interview_bot import InterviewBot
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from unidecode import unidecode

# ares/api/views/interview.py

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
from ares.api.services.interview_bot_service import InterviewBot # 🔹 InterviewBot 서비스 임포트
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


class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        keyword = request.data.get('keyword', '')
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class StartInterviewView(APIView):
    """면접을 시작하고 첫 질문을 반환하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company_name = request.data.get('company_name')
        job_title = request.data.get('job_title')

        if not all([company_name, job_title]):
            return Response({"error": "company_name and job_title are required"}, status=status.HTTP_400_BAD_REQUEST)

        company_description = get_company_description(company_name)
        
        bot = InterviewBot(job_title, company_name, company_description)
        first_question = bot.ask_first_question()
        
        request.session['interview_bot'] = bot.conversation_history
        request.session['interview_info'] = {
            'job_title': job_title,
            'company_name': company_name,
            'company_description': company_description,
        }

        return Response({"question": first_question}, status=status.HTTP_200_OK)


class AnalyzeAnswerView(APIView):
    """사용자의 답변을 분석하고 결과를 반환하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        answer = request.data.get('answer', '')
        if not answer:
            return Response({"error": "Answer is required"}, status=status.HTTP_400_BAD_REQUEST)

        conversation_history = request.session.get('interview_bot')
        interview_info = request.session.get('interview_info')

        if not conversation_history or not interview_info:
            return Response({"error": "Interview session not found. Please start the interview first."}, status=status.HTTP_400_BAD_REQUEST)

        bot = InterviewBot(
            job_title=interview_info['job_title'],
            company_name=interview_info['company_name'],
            company_description=interview_info['company_description']
        )
        bot.conversation_history = conversation_history
        
        current_question = bot.conversation_history[-1]['question']
        analysis_result = bot.analyze_answer(current_question, answer)
        
        request.session['interview_bot'] = bot.conversation_history

        return Response(analysis_result, status=status.HTTP_200_OK)



# ===== 내부 유틸 =====
def _ncs_query_from_meta(meta: dict | None) -> str:
    if not meta:
        return ""
    if (q := (meta.get("ncs_query") or "").strip()):
        return q
    role = (meta.get("role") or meta.get("job_title") or "").strip()
    division = (meta.get("division") or "").strip()
    company = (meta.get("company") or meta.get("name") or "").strip()
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
    q = _ncs_query_from_meta(meta)
    if not q:
        return {"ncs": [], "ncs_query": ""}

    items: List[Dict[str, Any]] = []

    if search_ncs_hybrid_semantic:
        try:
            sem = search_ncs_hybrid_semantic(q, top_k=top_k)
            items = sem.get("results", []) if isinstance(sem, dict) else (sem or [])
        except Exception as e:
            log.warning(f"[NCS] semantic failed ({e}), fallback to hybrid")

    if not items and search_ncs_hybrid:
        try:
            items = search_ncs_hybrid(q, top_k=top_k) or []
        except Exception as e:
            log.warning(f"[NCS] hybrid failed ({e})")

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
        difficulty = _normalize_difficulty(v.get("difficulty"))
        language = (v.get("language") or "ko").lower()
        meta = v.get("meta", {})
        ncs_context_in = v.get("ncs_context", {})

        use_rag_mode = (difficulty == 'hard')
        question_text = ""
        ncs_ctx = {}
        rag_context_to_save = {}

        if use_rag_mode:
            company_name = meta.get('company', '') or meta.get('name', '')
            job_title = meta.get('role', '') or meta.get('job_title', '')
            if not company_name:
                return Response({"error": "Company name is required for RAG mode"}, status=status.HTTP_400_BAD_REQUEST)
            
            safe_company_name_for_index = unidecode(company_name.lower()).replace(' ', '-')
            index_name = f"{safe_company_name_for_index}-report-index"
            container_name = os.getenv('AZURE_BLOB_CONTAINER', 'interview-data')

            rag_bot = RAGInterviewBot(
                company_name=company_name,
                job_title=job_title,
                container_name=container_name,
                index_name=index_name,
                ncs_context=ncs_ctx,
                jd_context=jd_context,
                resume_context=resume_context,
                research_context=research_context
            )
            
            if not rag_bot.rag_ready:
                return Response({"error": "RAG system not ready. Check Azure configurations or if documents are indexed."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            questions = rag_bot.generate_questions(num_questions=1)
            question_text = questions[0] if questions else ""
            if not question_text:
                 return Response({"error": "Failed to generate RAG-based question."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            rag_context_to_save = {
                'company_name': company_name,
                'job_title': job_title,
                'container_name': container_name,
                'index_name': index_name,
                'ncs_context': ncs_ctx,
                'jd_context': jd_context # Save jd_context in rag_context_to_save
            }

        else:
            context = join_texts(
                f"## [공고/JD]\n{jd_context}".strip(),
                f"## [지원서]\n{resume_context}".strip(),
                f"## [지원자 리서치]\n{research_context}".strip(),
            )
            
            ncs_ctx = ncs_context_in
            if not ncs_ctx.get("ncs"):
                ncs_ctx = _make_ncs_context(meta, top_k=5)

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

        session = InterviewSession.objects.create(
            user=request.user if getattr(request.user, "is_authenticated", False) else None,
            jd_context=jd_context,
            resume_context=resume_context,
            ncs_query=ncs_ctx.get("ncs_query", ""),
            meta=meta,
            context=ncs_ctx,
            rag_context=rag_context_to_save,
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

        session_id = v.get("session_id")
        if not session_id:
            return Response({"error": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=404)

        rag_mode = bool(session.rag_context)
        if rag_mode:
            rag_info = session.rag_context
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get('company_name', ''),
                job_title=rag_info.get('job_title', ''),
                container_name=rag_info.get('container_name', ''),
                index_name=rag_info.get('index_name', ''),
                ncs_context=rag_info.get('ncs_context', {}) # Pass ncs_context
            )
            
            if not rag_bot.rag_ready:
                return Response({"error": "RAG system not ready for follow-up questions."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            last_cand = session.turns.filter(role=InterviewTurn.Role.CANDIDATE).order_by("-turn_index").first()
            last_answer = last_cand.answer if last_cand else ""
            last_question = last_cand.question if last_cand else ""

            followups = [rag_bot.generate_follow_up_question(last_question, last_answer, {})]
            followups = [f for f in followups if f] or ["이전 답변에 대해 더 자세히 설명해주시겠습니까?"]

        else:
            last_cand = session.turns.filter(role=InterviewTurn.Role.CANDIDATE).order_by("-turn_index").first()
            if not last_cand:
                return Response({"error": "No previous answer found to generate a follow-up question."}, status=400)

            try:
                followups = interview_service.generate_followups(
                    main_q=last_cand.question or "",
                    answer=last_cand.answer or "",
                    k=4,
                    meta=session.meta or {},
                    ncs_query=session.ncs_query,
                )
            except Exception as e:
                log.exception("generate_followups failed: %s", e)
                return Response({"error": "꼬리질문 생성 실패"}, status=500)

        last = session.turns.order_by("-turn_index").first()
        turn = InterviewTurn.objects.create(
            session=session,
            turn_index=(0 if not last else last.turn_index + 1),
            role=InterviewTurn.Role.INTERVIEWER,
            question="",
            followups=followups,
        )

        out = InterviewNextOut({
            "session_id": session.id,
            "turn_index": turn.turn_index,
            "followups": followups,
        })
        return Response(out.data, status=200)


class InterviewSubmitAnswerAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewAnswerIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        session_id = v.get("session_id")
        if not session_id:
            return Response({"error": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        question = v.get("question", "")
        answer = v["answer"]

        try:
            session = InterviewSession.objects.get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션이거나 종료됨"}, status=404)

        # 🔹 RAG 모드 (난이도 hard)인 경우의 분기 처리는 유지
        rag_mode = bool(session.rag_context)
        if rag_mode:
            rag_info = session.rag_context
            rag_bot = RAGInterviewBot(
                company_name=rag_info.get('company_name', ''),
                job_title=rag_info.get('job_title', ''),
                container_name=rag_info.get('container_name', ''),
                index_name=rag_info.get('index_name', ''),
                ncs_context=rag_info.get('ncs_context', {}) # Pass ncs_context
            )
            
            if not rag_bot.rag_ready:
                return Response({"error": "RAG system not ready for answer analysis."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            analysis_result = rag_bot.analyze_answer_with_rag(question, answer)
            # RAG 모드의 응답 형식도 통일성을 위해 InterviewAnswerOut 사용을 고려할 수 있음
            return Response(analysis_result, status=status.HTTP_200_OK)

        # 🔹 일반 모드 (기존 로직을 새로운 InterviewBot으로 교체)
        else:
            req_turn_idx = v.get("turn_index")
            if req_turn_idx is not None:
                try:
                    qturn = session.turns.get(
                        turn_index=req_turn_idx, role=InterviewTurn.Role.INTERVIEWER
                    )
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

            # 🔹 InterviewBot 인스턴스 생성
            meta = session.meta or {}
            company_name = meta.get("company") or meta.get("name", "")
            job_title = meta.get("role") or meta.get("job_title", "")
            bot = InterviewBot(company_name=company_name, job_title=job_title)

            # 🔹 봇을 통해 답변 분석
            analysis_result = bot.analyze_answer(
                question=question_db, 
                answer=answer,
                ncs_query=session.ncs_query or _ncs_query_from_meta(meta)
            )

            if "error" in analysis_result:
                return Response(analysis_result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 🔹 분석 결과를 DB에 저장
            cand_turn.scores = analysis_result  # 전체 분석 결과를 JSONField에 저장
            cand_turn.feedback = analysis_result.get("feedback", "") # 별도 feedback 필드에도 저장
            cand_turn.save(update_fields=["scores", "feedback"])

            # 🔹 새로운 Serializer로 응답 반환
            response_data = {
                "ok": True,
                "session_id": session.id,
                "turn_index": cand_turn.turn_index,
                **analysis_result
            }
            out = InterviewAnswerOut(data=response_data)
            out.is_valid(raise_exception=True)
            return Response(out.validated_data, status=status.HTTP_200_OK)

        return Response({"error": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data

        session_id = v.get("session_id")

        if session_id:
            try:
                session = InterviewSession.objects.get(
                    id=session_id, status=InterviewSession.Status.ACTIVE
                )
            except InterviewSession.DoesNotExist:
                return Response({"detail": "유효하지 않은 세션이거나 이미 종료됨"}, status=404)

            session.report_id = f"report-{session.id}"
            session.status = InterviewSession.Status.FINISHED
            from django.utils import timezone
            session.finished_at = timezone.now()
            session.save(update_fields=["report_id", "status", "finished_at"])

            return Response(InterviewFinishOut({
                "report_id": session.report_id,
                "status": session.status,
            }).data, status=202)

        report_id = f"rep_{uuid.uuid4().hex[:12]}"
        return Response(InterviewFinishOut({
            "report_id": report_id,
            "status": "queued",
        }).data, status=202)

class InterviewReportAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "Interview session not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve all turns for the session
        turns = session.turns.all().order_by('turn_index')

        # Prepare transcript for report generation
        interview_transcript = []
        for turn in turns:
            if turn.role == InterviewTurn.Role.CANDIDATE: # Only process candidate turns for analysis
                interview_transcript.append({
                    "question_num": turn.turn_index,
                    "question": turn.question,
                    "answer": turn.answer,
                    "analysis": turn.scores, # Use scores field for analysis
                    "follow_up_question": "", # Not directly stored in turn, can be derived if needed
                    "follow_up_answer": "" # Not directly stored in turn
                })

        # Initialize RAGInterviewBot
        rag_info = session.rag_context
        if not rag_info: # Fallback for non-RAG mode sessions
            meta = session.meta or {}
            company_name = meta.get("company") or meta.get("name", "")
            job_title = meta.get("role") or meta.get("job_title", "")
        else:
            company_name = rag_info.get('company_name', '')
            job_title = rag_info.get('job_title', '')

        if not company_name or not job_title:
            return Response({"error": "Company name or job title not found in session context."}, status=status.HTTP_400_BAD_REQUEST)

        container_name = rag_info.get('container_name', os.getenv('AZURE_BLOB_CONTAINER', 'interview-data'))
        index_name = rag_info.get('index_name', f"{unidecode(company_name.lower()).replace(' ', '-')}-report-index")

        rag_bot = RAGInterviewBot(
            company_name=company_name,
            job_title=job_title,
            container_name=container_name,
            index_name=index_name
        )

        # Get resume_context from the session
        resume_context = session.resume_context

        # Generate final report using the modified RAGInterviewBot method
        try:
            final_report_data = rag_bot.generate_final_report(interview_transcript, resume_context)
        except Exception as e:
            log.exception("Failed to generate final report: %s", e)
            return Response({"error": "Failed to generate final report."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(final_report_data, status=status.HTTP_200_OK)
