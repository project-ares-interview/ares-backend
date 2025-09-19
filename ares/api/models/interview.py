# ares/api/models/interview.py

from __future__ import annotations
import uuid
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class InterviewSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FINISHED = "finished", "Finished"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="interview_sessions",
        db_index=True,  # 🔹 사용자별 세션 조회 빠르게
    )

    # 입력 컨텍스트
    meta = models.JSONField(default=dict, blank=True)          # {company, role, skills, jd_kpis ...}
    jd_context = models.TextField(blank=True, default="")
    resume_context = models.TextField(blank=True, default="")
    ncs_query = models.TextField(blank=True, default="")

    # 🔹 신규: 세션 컨텍스트/NCS 캐시 + 언어/난이도/모드
    context = models.JSONField(default=dict, blank=True)       # {"ncs":[...], "ncs_query":"..."}
    rag_context = models.JSONField(default=dict, blank=True)   # 🔹 RAG 모드 컨텍스트
    language = models.CharField(max_length=8, default="ko", db_index=True)        # "ko" | "en"
    difficulty = models.CharField(max_length=16, default="normal", db_index=True)  # "easy"|"normal"|"hard"

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # 수정된 부분: interviewer_mode 필드 추가
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    class InterviewerMode(models.TextChoices):
        TEAM_LEAD = 'team_lead', 'Team Lead'
        EXECUTIVE = 'executive', 'Executive'

    interviewer_mode = models.CharField(
        max_length=20,
        choices=InterviewerMode.choices,
        default=InterviewerMode.TEAM_LEAD,
        db_index=True,  # 🔹 면접관 모드별 통계/분석을 위해 인덱스 추가
        verbose_name="면접관 모드"
    )
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    
    # 상태
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,  # 🔹 진행중 세션 조회 최적화
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # 캐시/결과
    report_id = models.CharField(max_length=100, blank=True, default="")  # 리포트 참조용

    class Meta:
        db_table = "interview_session"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self) -> str:
        company = (self.meta or {}).get("company", "-")
        role = (self.meta or {}).get("role", "-")
        return f"InterviewSession({self.id}, {company} / {role})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class InterviewTurn(models.Model):
    """면접 대화의 각 차례(Turn)를 나타냅니다."""

    class Role(models.TextChoices):
        INTERVIEWER = "INTERVIEWER", "면접관"
        CANDIDATE = "CANDIDATE", "지원자"

    session = models.ForeignKey(
        InterviewSession, on_delete=models.CASCADE, related_name="turns"
    )
    turn_index = models.PositiveIntegerField(help_text="내부 정렬을 위한 숫자 인덱스")
    turn_label = models.CharField(
        max_length=10, default="0", help_text="사용자에게 보여질 순번 (예: '1', '1-1')"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    question = models.TextField(blank=True, null=True)
    answer = models.TextField(blank=True, null=True)
    scores = models.JSONField(blank=True, null=True)
    feedback = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("session", "turn_index")
        ordering = ["turn_index"]

    def __str__(self):
        return f"Turn {self.turn_label} ({self.role}) for Session {self.session.id}"