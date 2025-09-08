from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.profile.career import Career as ProfileCareer
from ares.api.models.profile.education import Education as ProfileEducation
from ares.api.models.resume.base import Resume
from ares.api.models.resume.career import ResumeCareer
from ares.api.models.resume.education import ResumeEducation
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
        """
        이력서 생성 시 현재 로그인한 사용자를 자동으로 할당하고,
        프로필의 학력/경력 정보를 복사합니다.
        """
        user = self.request.user
        resume = serializer.save(user=user)

        # 프로필 학력 정보 복사
        profile_educations = ProfileEducation.objects.filter(user=user)
        for edu in profile_educations:
            ResumeEducation.objects.create(
                resume=resume,
                school_type=edu.school_type,
                school_name=edu.school_name,
                major=edu.major,
                degree=edu.degree,
                status=edu.status,
                admission_date=edu.admission_date,
                graduation_date=edu.graduation_date,
            )

        # 프로필 경력 정보 복사
        profile_careers = ProfileCareer.objects.filter(user=user)
        for career in profile_careers:
            ResumeCareer.objects.create(
                resume=resume,
                company_name=career.company_name,
                experience_type=career.experience_type,
                is_attending=career.is_attending,
                start_date=career.start_date,
                end_date=career.end_date,
                department=career.department,
                responsibilities=career.responsibilities,
                task=career.task,
                reason_for_leaving=career.reason_for_leaving,
            )
