from django.conf import settings
from django.db import models


class MilitaryService(models.Model):
    class ServiceStatus(models.TextChoices):
        SERVED = "served", "군필"
        NOT_SERVED = "not_served", "미필"
        EXEMPTED = "exempted", "면제"
        SERVING = "serving", "복무중"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="military_service",
    )
    status = models.CharField(
        max_length=20,
        choices=ServiceStatus.choices,
        verbose_name="복무 상태",
    )

    def __str__(self):
        return f"{self.user.email} - {self.get_status_display()}"
