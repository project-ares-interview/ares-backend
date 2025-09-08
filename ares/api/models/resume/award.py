from django.db import models

from .base import Resume


class ResumeAward(models.Model):
    """이력서 수상/자격증"""

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="awards",
        help_text="수상 내역이 포함된 이력서",
    )
    title = models.CharField(max_length=100, help_text="수상 또는 자격증명")
    issuer = models.CharField(max_length=100, help_text="발급 기관")
    date_awarded = models.DateField(help_text="취득일")

    class Meta:
        ordering = ["-date_awarded"]
        verbose_name = "이력서 수상/자격증"
        verbose_name_plural = "이력서 수상/자격증 목록"
