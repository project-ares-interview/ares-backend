from __future__ import annotations
from rest_framework import serializers


class InterviewReportListOut(serializers.Serializer):
    id = serializers.UUIDField()
    session = serializers.UUIDField(allow_null=True, required=False)
    overall_summary = serializers.CharField(required=False, allow_blank=True)
    hiring_recommendation = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.DateTimeField()


class InterviewReportDetailOut(serializers.Serializer):
    id = serializers.UUIDField()
    session = serializers.UUIDField(allow_null=True, required=False)
    overall_summary = serializers.CharField(required=False, allow_blank=True)
    interview_flow_rationale = serializers.CharField(required=False, allow_blank=True)
    strengths_matrix = serializers.JSONField(required=False)
    weaknesses_matrix = serializers.JSONField(required=False)
    score_aggregation = serializers.JSONField(required=False)
    missed_opportunities = serializers.JSONField(required=False)
    potential_followups_global = serializers.JSONField(required=False)
    resume_feedback = serializers.JSONField(required=False)
    hiring_recommendation = serializers.CharField(required=False, allow_blank=True)
    next_actions = serializers.JSONField(required=False)
    question_by_question_feedback = serializers.JSONField(required=False)
    tags = serializers.JSONField(required=False)
    version = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
