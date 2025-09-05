from django.conf import settings
from django.db import models


class Patriot(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patriot",
    )
    patriot_code = models.CharField(
        max_length=50,
        verbose_name="보훈 코드",
        help_text="보훈 대상자일 경우 해당 코드를 입력합니다.",
    )

    def __str__(self):
        return f"{self.user.email} - {self.patriot_code}"
