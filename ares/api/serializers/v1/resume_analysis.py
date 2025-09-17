# ares/api/serializers/v1/resume_analysis.py
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer, OpenApiExample


class CompanyDataSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    department = serializers.CharField(max_length=100, required=False, allow_blank=True)
    job_title = serializers.CharField(max_length=100)
    location = serializers.CharField(max_length=100, required=False, allow_blank=True)
    kpi = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)
    requirements = serializers.ListField(child=serializers.CharField(max_length=500), required=False, default=list)


class ResumeAnalysisInSerializer(serializers.Serializer):
    jd_file = serializers.FileField(required=False, allow_null=True)
    resume_file = serializers.FileField(required=False, allow_null=True)
    research_file = serializers.FileField(required=False, allow_null=True)

    jd_text = serializers.CharField(required=False, allow_blank=True)
    resume_text = serializers.CharField(required=False, allow_blank=True)
    research_text = serializers.CharField(required=False, allow_blank=True)

    company = serializers.CharField() # JSON string


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            'Success Response',
            value={
                "overall_score": 88.5,
                "fit_analysis": {
                    "summary": "Candidate seems to be a good fit for the role.",
                    "details": "..."
                },
                "strength_analysis": {
                    "summary": "Strong background in Python and Django.",
                    "details": "..."
                },
                "weakness_analysis": {
                    "summary": "Limited experience with cloud infrastructure.",
                    "details": "..."
                },
                "ncs_context": {
                    "ncs_query": "...",
                    "ncs": []
                },
                "input_contexts": {
                    "refined": {
                        "jd_context": "...",
                        "resume_context": "...",
                        "research_context": "...",
                        "meta": {},
                        "ncs_context": {}
                    }
                }
            },
            response_only=True,
        )
    ]
)
class ResumeAnalysisOutSerializer(serializers.Serializer):
    overall_score = serializers.FloatField(required=False)
    fit_analysis = serializers.DictField(required=False)
    strength_analysis = serializers.DictField(required=False)
    weakness_analysis = serializers.DictField(required=False)
    ncs_context = serializers.DictField(required=False)
    input_contexts = serializers.DictField(required=False)
