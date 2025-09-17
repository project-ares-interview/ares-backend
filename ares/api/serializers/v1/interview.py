# ares/api/serializers/v1/interview.py
from __future__ import annotations
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer, OpenApiExample

# ===== 공통 =====
class MetaIn(serializers.Serializer):
    company = serializers.CharField(required=False, allow_blank=True, default="")
    name = serializers.CharField(required=False, allow_blank=True, default="")
    division = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.CharField(required=False, allow_blank=True, default="")
    job_title = serializers.CharField(required=False, allow_blank=True, default="")
    skills = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    jd_kpis = serializers.ListField(child=serializers.CharField(), required=False, default=list)

# ===== Start =====
class InterviewStartIn(serializers.Serializer):
    jd_context = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)
    resume_context = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)
    research_context = serializers.CharField(required=False, allow_blank=True, default="")
    difficulty = serializers.ChoiceField(choices=["easy", "normal", "hard"], required=False, default="normal")
    language = serializers.ChoiceField(choices=["ko", "en"], required=False, default="ko")
    interviewer_mode = serializers.ChoiceField(choices=["team_lead", "executive"], required=False, default="team_lead")
    meta = MetaIn(required=False, default=dict)
    ncs_context = serializers.JSONField(required=False, default=dict)

class InterviewStartOut(serializers.Serializer):
    message = serializers.CharField()
    question = serializers.CharField()
    session_id = serializers.UUIDField()
    turn_index = serializers.IntegerField()
    context = serializers.JSONField(required=False)
    language = serializers.CharField(required=False, default="ko")
    difficulty = serializers.CharField(required=False, default="normal")
    interviewer_mode = serializers.CharField(required=False, default="team_lead")

# ===== Next =====
class InterviewNextIn(serializers.Serializer):
    session_id = serializers.UUIDField(required=True)

@extend_schema_serializer(
    examples=[
        OpenApiExample(
            'Next Question',
            value={
                "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "turn_index": 2,
                "question": "Tell me about a time you handled a difficult stakeholder.",
                "done": False,
            },
            response_only=True,
        ),
        OpenApiExample(
            'Interview Finished',
            value={
                "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "turn_index": None,
                "question": None,
                "done": True,
            },
            response_only=True,
        ),
    ]
)
class InterviewNextOut(serializers.Serializer):
    session_id = serializers.UUIDField()
    turn_index = serializers.IntegerField(allow_null=True)
    question = serializers.CharField(allow_null=True)
    done = serializers.BooleanField()

# ===== Answer =====
class InterviewAnswerIn(serializers.Serializer):
    session_id = serializers.UUIDField(required=True)
    question = serializers.CharField(required=True)
    answer = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)

    def validate_answer(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 5:
            raise serializers.ValidationError("답변은 최소 5자 이상 입력하세요.")
        return v

class InterviewAnswerAnalysis(serializers.Serializer):
    structured = serializers.DictField()
    rag_analysis = serializers.DictField()

class InterviewAnswerOut(serializers.Serializer):
    analysis = InterviewAnswerAnalysis()
    followups_buffered = serializers.ListField(child=serializers.CharField())
    message = serializers.CharField()

# ===== Finish =====
class InterviewFinishIn(serializers.Serializer):
    session_id = serializers.UUIDField(required=True)

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