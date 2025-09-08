from django.conf import settings
from django.db import models
from ordered_model.models import OrderedModel


class Career(OrderedModel):
    class ExperienceType(models.TextChoices):
        NEWCOMER = "newcomer", "신입"
        EXPERIENCED = "experienced", "경력"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="careers",
    )
    company_name = models.CharField(
        max_length=100,
        verbose_name="회사명",
    )
    experience_type = models.CharField(
        max_length=20,
        choices=ExperienceType.choices,
        verbose_name="신입/경력",
    )
    is_attending = models.BooleanField(
        default=False,
        verbose_name="재직중 여부",
    )
    start_date = models.DateField(
        verbose_name="입사일",
    )
    end_date = models.DateField(
        verbose_name="퇴사일",
        blank=True,
        null=True,
    )
    department = models.CharField(
        max_length=100,
        verbose_name="부서",
        blank=True,
    )
    responsibilities = models.TextField(
        verbose_name="직위/직책",
        blank=True,
    )
    task = models.TextField(
        verbose_name="담당 업무",
        blank=True,
    )
    reason_for_leaving = models.CharField(
        max_length=255,
        verbose_name="퇴사 사유",
        blank=True,
    )

    order_with_respect_to = "user"

    class Meta(OrderedModel.Meta):
        pass

    def __str__(self):
        return f"{self.user.email} - {self.company_name}"
