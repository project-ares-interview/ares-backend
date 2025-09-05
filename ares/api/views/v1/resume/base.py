from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.resume.base import Resume
from ares.api.serializers.v1.resume.base import ResumeSerializer


class ResumeViewSet(viewsets.ModelViewSet):
    """
    이력서 템플릿 CRUD를 위한 ViewSet.
    """

    serializer_class = ResumeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """로그인한 사용자의 이력서만 반환합니다."""
        return Resume.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """이력서 생성 시 현재 로그인한 사용자를 자동으로 할당합니다."""
        serializer.save(user=self.request.user)
