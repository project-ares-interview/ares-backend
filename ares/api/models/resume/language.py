from django.db import models

from .base import Resume


class ResumeLanguage(models.Model):
    """이력서 외국어 능력"""

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="languages",
        help_text="외국어 능력이 포함된 이력서",
    )
    language = models.CharField(max_length=50, help_text="언어명 (예: English)")
    proficiency = models.CharField(max_length=50, help_text="구사 수준 (예: Native, Fluent)")

    class Meta:
        verbose_name = "이력서 외국어"
        verbose_name_plural = "이력서 외국어 목록"
