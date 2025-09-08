from django.db import models
from ordered_model.models import OrderedModel

from .base import Resume


class ResumeCareer(OrderedModel):
    """이력서 경력 사항"""

    class ExperienceType(models.TextChoices):
        NEWCOMER = "newcomer", "신입"
        EXPERIENCED = "experienced", "경력"

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="careers",
        help_text="경력 사항이 포함된 이력서",
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

    order_with_respect_to = "resume"

    class Meta(OrderedModel.Meta):
        pass
