# ares/api/services/prompts/__init__.py
"""
ARES Prompt Suite - Re-exports from submodules.
"""

from .base import (
    SYSTEM_RULES,
    prompt_json_output_only,
    INTERVIEWER_PERSONAS,
    SCORE_BOUNDS,
    DIFFICULTY_INSTRUCTIONS,
    QUESTION_TYPES,
    PHASES,
)
from .analysis import (
    prompt_identifier,
    prompt_extractor,
    prompt_scorer,
    prompt_score_explainer,
    prompt_coach,
    prompt_bias_checker,
    prompt_model_answer,
    prompt_rag_answer_analysis,
    prompt_jd_keyword_extractor,
    prompt_jd_preprocessor,
)
from .classification import prompt_intent_classifier, prompt_jd_classifier
from .design import (
    prompt_interview_designer,
    prompt_interview_designer_v2,
    prompt_resume_analyzer,
)
from .report import (
    prompt_rag_final_report,
    prompt_detailed_section,
    prompt_detailed_overview,
    prompt_thematic_summary,
)
from .question_generation import (
    prompt_rag_follow_up_question,
    prompt_followup_v2,
    prompt_icebreaker_question,
    prompt_self_introduction_question,
    prompt_motivation_question,
    prompt_soft_followup,
)
from .utility import (
    prompt_rag_json_correction,
    ORCHESTRATION_DOC,
    CACHE_KEYS,
    CACHE_TTLS,
    ICEBREAK_TEMPLATES_KO,
    INTRO_TEMPLATE_KO,
    MOTIVE_TEMPLATE_KO,
    WRAPUP_TEMPLATES_KO,
    make_icebreak_question_llm_or_template,
    make_intro_question_llm_or_template,
    make_motive_question_llm_or_template,
    make_wrapup_question_template,
)

__all__ = [
    # from base
    "SYSTEM_RULES",
    "prompt_json_output_only",
    "INTERVIEWER_PERSONAS",
    "SCORE_BOUNDS",
    "DIFFICULTY_INSTRUCTIONS",
    "QUESTION_TYPES",
    "PHASES",
    # from analysis
    "prompt_identifier",
    "prompt_extractor",
    "prompt_scorer",
    "prompt_score_explainer",
    "prompt_coach",
    "prompt_bias_checker",
    "prompt_model_answer",
    "prompt_rag_answer_analysis",
    "prompt_jd_keyword_extractor",
    "prompt_jd_preprocessor",
    # from classification
    "prompt_intent_classifier",
    "prompt_jd_classifier",
    # from design
    "prompt_interview_designer",
    "prompt_interview_designer_v2",
    "prompt_resume_analyzer",
    # from report
    "prompt_rag_final_report",
    "prompt_detailed_section",
    "prompt_detailed_overview",
    "prompt_thematic_summary",
    # from question_generation
    "prompt_rag_follow_up_question",
    "prompt_followup_v2",
    "prompt_icebreaker_question",
    "prompt_self_introduction_question",
    "prompt_motivation_question",
    "prompt_soft_followup",
    # from utility
    "prompt_rag_json_correction",
    "ORCHESTRATION_DOC",
    "CACHE_KEYS",
    "CACHE_TTLS",
    "ICEBREAK_TEMPLATES_KO",
    "INTRO_TEMPLATE_KO",
    "MOTIVE_TEMPLATE_KO",
    "WRAPUP_TEMPLATES_KO",
    "make_icebreak_question_llm_or_template",
    "make_intro_question_llm_or_template",
    "make_motive_question_llm_or_template",
    "make_wrapup_question_template",
]
