from .base import ResumeViewSet
from .award import ResumeAwardViewSet
from .career import ResumeCareerViewSet
from .education import ResumeEducationViewSet
from .language import ResumeLanguageViewSet
from .link import ResumeLinkViewSet

__all__ = [
    "ResumeViewSet",
    "ResumeAwardViewSet",
    "ResumeCareerViewSet",
    "ResumeEducationViewSet",
    "ResumeLanguageViewSet",
    "ResumeLinkViewSet",
]