from django.conf import settings
from django.db import models


class Education(models.Model):
    class SchoolType(models.TextChoices):
        HIGH_SCHOOL = "high_school", "고등학교"
        JUNIOR_COLLEGE = "junior_college", "대학교 (2-3년제)"
        UNIVERSITY = "university", "대학교 (4년제)"
        MASTER = "master", "석사"
        DOCTORATE = "doctorate", "박사"

    class AttendanceStatus(models.TextChoices):
        ATTENDING = "attending", "재학중"
        GRADUATED = "graduated", "졸업"
        COMPLETED = "completed", "수료"
        DROPOUT = "dropout", "중퇴"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="educations",
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
    )
    status = models.CharField(
        max_length=20,
        choices=AttendanceStatus.choices,
        verbose_name="재학 여부",
    )
    admission_date = models.CharField(
        max_length=7,
        verbose_name="입학일 (YYYY-MM)",
    )
    graduation_date = models.CharField(
        max_length=7,
        verbose_name="졸업일 (YYYY-MM)",
        blank=True,
        null=True,
    )
    order = models.PositiveIntegerField(verbose_name="정렬 순서")

    class Meta:
        ordering = ["order"]
        unique_together = ("user", "order")

    def __str__(self):
        return f"{self.user.email} - {self.school_name}"
