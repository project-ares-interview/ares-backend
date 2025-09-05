from django.db import models

from .base import Resume


class ResumeCareer(models.Model):
    """이력서 경력 사항"""

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="careers",
        help_text="경력 사항이 포함된 이력서",
    )
    company_name = models.CharField(max_length=100, help_text="회사명")
    department = models.CharField(max_length=100, help_text="부서명")
    role = models.CharField(max_length=100, help_text="직책 (예: 팀장, 팀원)")
    start_date = models.DateField(help_text="입사일")
    end_date = models.DateField(null=True, blank=True, help_text="퇴사일")
    is_current = models.BooleanField(default=False, help_text="현재 재직중 여부")
    responsibilities = models.TextField(help_text="담당 직무 및 성과")

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "이력서 경력"
        verbose_name_plural = "이력서 경력 목록"
