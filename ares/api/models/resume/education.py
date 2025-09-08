from django.db import models
from ordered_model.models import OrderedModel

from .base import Resume


class ResumeEducation(OrderedModel):
    """이력서 학력 사항"""

    class SchoolType(models.TextChoices):
        ELEMENTARY_SCHOOL = "elementary_school", "초등학교"
        MIDDLE_SCHOOL = "middle_school", "중학교"
        HIGH_SCHOOL = "high_school", "고등학교"
        JUNIOR_COLLEGE = "junior_college", "대학교 (2-3년제)"
        UNIVERSITY = "university", "대학교 (4년제)"

    class DegreeType(models.TextChoices):
        ASSOCIATE = "associate", "전문학사"
        BACHELOR = "bachelor", "학사"
        MASTER = "master", "석사"
        DOCTORATE = "doctorate", "박사"

    class AttendanceStatus(models.TextChoices):
        ATTENDING = "attending", "재학중"
        GRADUATED = "graduated", "졸업"
        COMPLETED = "completed", "수료"
        DROPOUT = "dropout", "중퇴"

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="educations",
        help_text="학력 사항이 포함된 이력서",
    )
    school_type = models.CharField(
        max_length=20,
        choices=SchoolType.choices,
        verbose_name="학교 종류",
    )
    school_name = models.CharField(
        max_length=100,
        verbose_name="학교명",
    )
    major = models.CharField(
        max_length=100,
        verbose_name="전공명",
        blank=True,
        null=True,
    )
    degree = models.CharField(
        max_length=50,
        verbose_name="학위",
        choices=DegreeType.choices,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=AttendanceStatus.choices,
        verbose_name="재학 여부",
    )
    admission_date = models.DateField(
        verbose_name="입학일 (YYYY-MM)",
    )
    graduation_date = models.DateField(
        verbose_name="졸업일 (YYYY-MM)",
        blank=True,
        null=True,
    )

    order_with_respect_to = "resume"

    class Meta(OrderedModel.Meta):
        pass
