from .base import ResumeSerializer
from .award import ResumeAwardSerializer
from .career import ResumeCareerSerializer
from .education import ResumeEducationSerializer
from .language import ResumeLanguageSerializer
from .link import ResumeLinkSerializer

__all__ = [
    "ResumeSerializer",
    "ResumeAwardSerializer",
    "ResumeCareerSerializer",
    "ResumeEducationSerializer",
    "ResumeLanguageSerializer",
    "ResumeLinkSerializer",
]
