# ares/api/models/__init__.py
from .cover_letter import CoverLetter
from .user import User
from .interview import InterviewSession, InterviewTurn   # ✅ 추가
from .interview_report import InterviewReport

__all__ = [
    "User",
    "CoverLetter",
    "InterviewSession",
    "InterviewTurn",
    "InterviewReport",
]
