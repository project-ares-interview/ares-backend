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

    # 🔹 신규: 세션 컨텍스트/NCS 캐시 + 언어/난이도
    context = models.JSONField(default=dict, blank=True)       # {"ncs":[...], "ncs_query":"..."}
    rag_context = models.JSONField(default=dict, blank=True)   # 🔹 RAG 모드 컨텍스트
    language = models.CharField(max_length=8, default="ko", db_index=True)        # "ko" | "en"
    difficulty = models.CharField(max_length=16, default="normal", db_index=True)  # "easy"|"normal"|"hard"

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
    class Role(models.TextChoices):
        INTERVIEWER = "interviewer", "Interviewer"
        CANDIDATE = "candidate", "Candidate"
        SYSTEM = "system", "System"

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="turns",
        db_index=True,
    )
    turn_index = models.PositiveIntegerField(db_index=True)  # 0부터 증가
    role = models.CharField(max_length=16, choices=Role.choices, db_index=True)

    question = models.TextField(blank=True, default="")
    answer = models.TextField(blank=True, default="")

    # 🔹 신규: 인터뷰어 턴의 꼬리질문 세트 저장
    #    뷰/시리얼라이저에서 List[str]을 기대한다면
    #    서비스단에서 [{"type":"why","text":"..."}] → ["..."]로 변환하여 저장하거나,
    #    아래 주석처럼 단순 List[str]로 운영해도 됨.
    followups = models.JSONField(default=list, blank=True)  # 예) ["왜 그렇게 판단했나요?", "근거를 설명해 보세요."]

    # 평가/피드백
    scores = models.JSONField(default=dict, blank=True)      # {"overall":3.7,"S":3,...}
    feedback = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "interview_turn"
        ordering = ["turn_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "turn_index"],
                name="unique_session_turnindex",
            )
        ]
        indexes = [
            models.Index(fields=["session", "turn_index"]),
        ]

    def __str__(self) -> str:
        # FK 컬럼명 자동 생성: session_id 사용 가능
        return f"Turn#{self.turn_index} {self.role} (session={self.session_id})"
