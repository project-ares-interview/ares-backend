# ares/api/views/v1/interview/report.py
import uuid
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession
from ares.api.serializers.v1.interview import InterviewReportOut
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)

class InterviewReportAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Get Interview Report",
        description="""
Retrieves the detailed report for a finished interview session.

- The `session_id` from the interview process is used as the report identifier.
- If the report has been generated previously, it's returned from the cache.
- If not, it will be generated on-demand based on the full interview transcript.
""",
        responses=InterviewReportOut
    )
    def get(self, request, session_id: uuid.UUID, *args, **kwargs):
        try:
            session = InterviewSession.objects.get(id=session_id)
        except InterviewSession.DoesNotExist:
            return Response({"detail": "세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        cached_report = (session.meta or {}).get("final_report")
        if cached_report:
            return Response(cached_report, status=status.HTTP_200_OK)

        # On-demand generation if not cached
        rag_info = session.rag_context or {}
        if not rag_info:
            return Response({"error": "RAG 컨텍스트가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        rag_bot = RAGInterviewBot(
            company_name=rag_info.get("company_name", ""), job_title=rag_info.get("job_title", ""),
            container_name=rag_info.get("container_name", ""), index_name=rag_info.get("index_name", ""),
            interviewer_mode=session.interviewer_mode, resume_context=session.resume_context,
            ncs_context=session.context or {}, jd_context=session.jd_context or "",
        )
        
        turns = session.turns.order_by("turn_index").all()
        transcript = []
        structured_scores = []
        
        # 더 안정적인 transcript 및 structured_scores 생성 로직
        current_turn = {}
        for t in turns:
            if t.role == "INTERVIEWER":
                # 이전 턴이 완성되지 않았다면 transcript에 추가
                if current_turn:
                    transcript.append(current_turn)
                current_turn = {"question": t.question, "answer": None, "analysis": None}
            elif t.role == "CANDIDATE":
                if not current_turn: # CANDIDATE 턴이 먼저 시작되는 예외 케이스 처리
                    current_turn = {"question": t.question, "answer": t.answer, "analysis": t.scores}
                else:
                    current_turn["answer"] = t.answer
                    current_turn["analysis"] = t.scores
                
                if t.scores: # 분석 점수가 있는 경우 structured_scores에 추가
                    structured_scores.append(t.scores)
                
                transcript.append(current_turn)
                current_turn = {} # 턴 완성 후 초기화

        # 마지막 턴이 INTERVIEWER로 끝나서 추가되지 않은 경우 처리
        if current_turn and "question" in current_turn:
            transcript.append(current_turn)

        final_report = rag_bot.build_final_report(transcript, structured_scores)

        session.meta = {**(session.meta or {}), "final_report": final_report}
        session.save(update_fields=["meta"])

        return Response(final_report, status=status.HTTP_200_OK)
