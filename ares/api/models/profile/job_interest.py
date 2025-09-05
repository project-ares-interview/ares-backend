from django.conf import settings
from django.db import models


class JobInterest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_interests",
    )
    job_title = models.CharField(
        max_length=100,
        verbose_name="관심 직무",
    )
    order = models.PositiveIntegerField(verbose_name="정렬 순서")

    class Meta:
        ordering = ["order"]
        unique_together = (("user", "job_title"), ("user", "order"))

    def __str__(self):
        return f"{self.user.email} - {self.job_title}"
