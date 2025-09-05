from django.db import models

from .base import Resume


class ResumeEducation(models.Model):
    """이력서 학력 사항"""

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="educations",
        help_text="학력 사항이 포함된 이력서",
    )
    school_name = models.CharField(max_length=100, help_text="학교명")
    major = models.CharField(max_length=100, help_text="전공")
    degree = models.CharField(max_length=50, help_text="학위 (예: 학사, 석사)")
    start_date = models.DateField(help_text="입학일")
    end_date = models.DateField(null=True, blank=True, help_text="졸업일")
    courses_taken = models.TextField(blank=True, help_text="이수 과목 또는 연구 내용")

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "이력서 학력"
        verbose_name_plural = "이력서 학력 목록"
