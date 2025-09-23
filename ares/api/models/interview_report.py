from __future__ import annotations
import uuid
from django.db import models
from django.contrib.auth import get_user_model

from .interview import InterviewSession

User = get_user_model()


class InterviewReport(models.Model):
    """면접 분석 리포트 저장 모델."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="interview_reports",
        db_index=True,
    )
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="reports",
        null=True,
        blank=True,
        db_index=True,
    )

    # 주요 필드 (요약 및 세부 지표)
    overall_summary = models.TextField(blank=True, default="")
    interview_flow_rationale = models.TextField(blank=True, default="")
    strengths_matrix = models.JSONField(default=list, blank=True)
    weaknesses_matrix = models.JSONField(default=list, blank=True)
    score_aggregation = models.JSONField(default=dict, blank=True)
    missed_opportunities = models.JSONField(default=list, blank=True)
    potential_followups_global = models.JSONField(default=list, blank=True)
    resume_feedback = models.JSONField(default=dict, blank=True)
    hiring_recommendation = models.CharField(max_length=32, blank=True, default="")
    next_actions = models.JSONField(default=list, blank=True)
    question_by_question_feedback = models.JSONField(default=list, blank=True)

    # 메타
    tags = models.JSONField(default=list, blank=True)
    version = models.CharField(max_length=50, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "interview_report"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "session"], name="uniq_report_user_session")
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["session"]),
        ]

    def __str__(self) -> str:
        return f"InterviewReport({self.id}) for session={getattr(self.session, 'id', None)} user={getattr(self.user, 'id', None)}"
