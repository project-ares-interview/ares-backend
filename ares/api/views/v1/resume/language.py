from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.resume.base import Resume
from ares.api.models.resume.language import ResumeLanguage
from ares.api.serializers.v1.resume.language import ResumeLanguageSerializer


class ResumeLanguageViewSet(viewsets.ModelViewSet):
    """특정 이력서에 대한 외국어 능력 CRUD를 위한 ViewSet"""

    serializer_class = ResumeLanguageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        요청된 resume_pk에 해당하는 이력서의 외국어 능력을 반환합니다.
        해당 이력서가 요청을 보낸 사용자의 소유인지 확인합니다.
        """
        resume = get_object_or_404(
            Resume,
            pk=self.kwargs["resume_pk"],
            user=self.request.user,
        )
        return ResumeLanguage.objects.filter(resume=resume)

    def perform_create(self, serializer):
        """
        외국어 능력 생성 시 URL의 resume_pk를 사용하여 이력서에 자동으로 할당합니다.
        """
        resume = get_object_or_404(
            Resume,
            pk=self.kwargs["resume_pk"],
            user=self.request.user,
        )
        serializer.save(resume=resume)
