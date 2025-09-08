from django.db import models

from .base import Resume


class ResumeLink(models.Model):
    """이력서 관련 링크"""

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="links",
        help_text="링크가 포함된 이력서",
    )
    title = models.CharField(max_length=100, help_text="링크 제목 (예: GitHub, 포트폴리오)")
    url = models.URLField(help_text="링크 주소")

    class Meta:
        verbose_name = "이력서 링크"
        verbose_name_plural = "이력서 링크 목록"
