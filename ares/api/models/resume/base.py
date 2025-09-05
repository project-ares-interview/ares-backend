from django.conf import settings
from django.db import models


class Resume(models.Model):
    """이력서 템플릿의 기본 정보"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="resumes",
        help_text="이력서 작성자",
    )
    title = models.CharField(
        max_length=100,
        help_text="이력서 제목 (예: 백엔드 개발자 지원용)",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="생성 일시")
    updated_at = models.DateTimeField(auto_now=True, help_text="최종 수정 일시")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "이력서"
        verbose_name_plural = "이력서 목록"

    def __str__(self):
        return f"{self.user.name} - {self.title}"
