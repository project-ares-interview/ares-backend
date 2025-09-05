from django.conf import settings
from django.db import models


class Disability(models.Model):
    class Severity(models.TextChoices):
        MILD = "mild", "경증"
        SEVERE = "severe", "중증"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="disabilities",
    )
    disability_type = models.CharField(
        max_length=100,
        verbose_name="장애 종류",
    )
    severity = models.CharField(
        max_length=10,
        choices=Severity.choices,
        verbose_name="장애 중증도",
    )
    order = models.PositiveIntegerField(verbose_name="정렬 순서")

    class Meta:
        ordering = ["order"]
        unique_together = ("user", "order")

    def __str__(self):
        return f"{self.user.email} - {self.disability_type} ({self.get_severity_display()})"
