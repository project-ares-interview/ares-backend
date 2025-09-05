from django.conf import settings
from django.db import models


class CoverLetter(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cover_letters",
        help_text="자기소개서 작성자",
    )
    title = models.CharField(
        max_length=100,
        help_text="자기소개서 제목",
    )
    content = models.TextField(
        help_text="자기소개서 내용",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="생성 일시",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "자기소개서"
        verbose_name_plural = "자기소개서"

    def __str__(self):
        return f"{self.user.name} - {self.title}"
