# ares/api/serializers/v1/resume_analysis.py
from rest_framework import serializers


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
