# ares/api/views/v1/interview/finish.py
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.models import InterviewSession
from ares.api.serializers.v1.interview import InterviewFinishIn, InterviewFinishOut
from ares.api.services.final_report_service import finalize_interview_session
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)


class InterviewFinishAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Finish an Interview",
        description="""
Marks an interview session as finished and triggers the generation of the final report.

- This endpoint should be called when the candidate has completed all questions.
- The backend compiles the entire interview transcript, generates a comprehensive report, and saves it.
- The response will contain the session ID, which now also serves as the report ID.
""",
        request=InterviewFinishIn,
        responses=InterviewFinishOut,
    )
    def post(self, request, *args, **kwargs):
        s = InterviewFinishIn(data=request.data)
        s.is_valid(raise_exception=True)
        session_id = s.validated_data["session_id"]

        try:
            session = InterviewSession.objects.select_related("user__profile").get(
                id=session_id, status=InterviewSession.Status.ACTIVE
            )
        except InterviewSession.DoesNotExist:
            return Response({"detail": "유효하지 않은 세션입니다."}, status=status.HTTP_404_NOT_FOUND)

        try:
            finalize_interview_session(session)
        except Exception as e:
            log.exception("[%s] Failed to finalize interview session", session_id)
            return Response({"detail": f"리포트 생성 실패: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        out = InterviewFinishOut({"report_id": str(session.id), "status": session.status})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)
