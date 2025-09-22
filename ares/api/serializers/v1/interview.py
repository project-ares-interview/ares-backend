# ares/api/serializers/v1/interview.py
from __future__ import annotations
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer, OpenApiExample

# ===== 공통 =====
class MetaIn(serializers.Serializer):
    company_name = serializers.CharField(required=False, allow_blank=True, default="")
    person_name = serializers.CharField(required=False, allow_blank=True, default="")
    division = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.CharField(required=False, allow_blank=True, default="")
    job_title = serializers.CharField(required=False, allow_blank=True, default="")
    skills = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    jd_kpis = serializers.ListField(child=serializers.CharField(), required=False, default=list)

# ===== Start =====
class InterviewStartIn(serializers.Serializer):
    jd_context = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
        help_text="The full text of the job description."
    )
    resume_context = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
        help_text="The full text of the candidate's resume."
    )
    research_context = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional research material about the company or role."
    )
    difficulty = serializers.ChoiceField(
        choices=["easy", "normal", "hard"],
        required=False,
        default="normal",
        help_text="The desired difficulty level for the interview."
    )
    language = serializers.ChoiceField(
        choices=["ko", "en"],
        required=False,
        default="ko",
        help_text="The language for the interview."
    )
    interviewer_mode = serializers.ChoiceField(
        choices=["team_lead", "executive"],
        required=False,
        default="team_lead",
        help_text="The persona of the AI interviewer."
    )
    meta = MetaIn(
        required=False,
        default=dict,
        help_text="Additional metadata about the company and role."
    )
    ncs_context = serializers.JSONField(
        required=False,
        default=dict,
        help_text="NCS (National Competency Standards) context for the interview."
    )

class InterviewStartOut(serializers.Serializer):
    message = serializers.CharField()
    question = serializers.CharField()
    session_id = serializers.UUIDField()
    turn_label = serializers.CharField(help_text="The label of the turn, e.g., '1'.")
    context = serializers.JSONField(required=False)
    language = serializers.CharField(required=False, default="ko")
    difficulty = serializers.CharField(required=False, default="normal")
    interviewer_mode = serializers.CharField(required=False, default="team_lead")

# ===== Next =====
class InterviewNextIn(serializers.Serializer):
    session_id = serializers.UUIDField(
        required=True,
        help_text="The unique identifier for the active interview session."
    )
    include_followups = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Whether to include follow-up questions in the response."
    )

@extend_schema_serializer(
    examples=[
        OpenApiExample(
            'Next Question',
            value={
                "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "turn_label": "2",
                "question": "Tell me about a time you handled a difficult stakeholder.",
                "followups": [],
                "done": False,
            },
            response_only=True,
        ),
        OpenApiExample(
            'Interview Finished',
            value={
                "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "turn_label": None,
                "question": None,
                "followups": [],
                "done": True,
            },
            response_only=True,
        ),
    ]
)
class InterviewNextOut(serializers.Serializer):
    session_id = serializers.UUIDField()
    turn_label = serializers.CharField(allow_null=True, help_text="The label of the turn, e.g., '2' or '2-1'.")
    question = serializers.CharField(allow_null=True)
    followups = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    done = serializers.BooleanField()

# ===== Answer =====
class InterviewAnswerIn(serializers.Serializer):
    session_id = serializers.UUIDField(
        required=True,
        help_text="The unique identifier for the active interview session."
    )
    question = serializers.CharField(
        required=True,
        help_text="The question that was asked to the candidate."
    )
    answer = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        help_text="The candidate's answer to the question. Must be at least 5 characters long."
    )

    def validate_answer(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 5:
            raise serializers.ValidationError("답변은 최소 5자 이상 입력하세요.")
        return v

class NextQuestionSerializer(serializers.Serializer):
    turn_label = serializers.CharField(allow_null=True)
    question = serializers.CharField(allow_null=True)

class InterviewAnswerOut(serializers.Serializer):
    analysis = serializers.DictField(help_text="The detailed analysis of the candidate's answer.")
    transition_phrase = serializers.CharField(allow_null=True, help_text="A smooth transition phrase to the next question.")
    next_question = NextQuestionSerializer(allow_null=True, help_text="The next question to be asked.")
    message = serializers.CharField()
    turn_label = serializers.CharField(help_text="The label of the answer that was just submitted, e.g., '1-1'.")

# ===== Finish =====
class InterviewFinishIn(serializers.Serializer):
    session_id = serializers.UUIDField(
        required=True,
        help_text="The unique identifier for the interview session to be finished."
    )

class InterviewFinishOut(serializers.Serializer):
    report_id = serializers.CharField()
    status = serializers.CharField()

# ===== Report =====
class InterviewReportOut(serializers.Serializer):
    overall_summary = serializers.CharField()
    interview_flow_rationale = serializers.CharField()
    strengths_matrix = serializers.ListField(child=serializers.DictField())
    weaknesses_matrix = serializers.ListField(child=serializers.DictField())
    score_aggregation = serializers.DictField()
    missed_opportunities = serializers.ListField(child=serializers.CharField())
    potential_followups_global = serializers.ListField(child=serializers.CharField())
    resume_feedback = serializers.DictField()
    hiring_recommendation = serializers.ChoiceField(choices=["strong_hire", "hire", "no_hire"])
    next_actions = serializers.ListField(child=serializers.CharField())
    question_by_question_feedback = serializers.ListField(child=serializers.DictField())

# ===== Find Companies =====
class FindCompaniesRequestSerializer(serializers.Serializer):
    keyword = serializers.CharField()

class FindCompaniesResponseSerializer(serializers.Serializer):
    companies = serializers.ListField(child=serializers.CharField())
