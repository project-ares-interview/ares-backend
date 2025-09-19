# ares/api/serializers/v1/resume_analysis.py
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer, OpenApiExample

class CompanyDataSerializer(serializers.Serializer):
    company_name = serializers.CharField(
        max_length=100,
        help_text="The name of the company."
    )
    department = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="The specific department within the company."
    )
    job_title = serializers.CharField(
        max_length=100,
        help_text="The title of the job."
    )
    location = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="The location of the job."
    )
    kpi = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
        help_text="Key Performance Indicators for the role."
    )
    requirements = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        default=list,
        help_text="Specific requirements for the job."
    )

class ResumeAnalysisInSerializer(serializers.Serializer):
    jd_file = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Upload the job description file (e.g., PDF, DOCX)."
    )
    resume_file = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Upload the resume file (e.g., PDF, DOCX)."
    )
    research_file = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Upload a research material file about the company (optional)."
    )

    jd_text = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Provide the job description as a string."
    )
    resume_text = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Provide the resume as a string."
    )
    research_text = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Provide research material about the company as a string (optional)."
    )

    company = serializers.JSONField(
        help_text='A JSON object containing company and job details. See CompanyDataSerializer for the expected structure. Can be passed as a JSON string or a direct object.'
    )

    # 하위호환: 'name'으로 들어오면 'company_name'으로 자동 매핑
    def validate_company(self, value):
        if isinstance(value, dict):
            if "company_name" not in value and "name" in value:
                value["company_name"] = value.pop("name")
            # 2차 검증: CompanyDataSerializer 스키마 강제
            s = CompanyDataSerializer(data=value)
            s.is_valid(raise_exception=True)
            return s.validated_data
        # 문자열로 온 경우(구버전 클라이언트): JSON 파싱 시도
        import json
        try:
            parsed = json.loads(value)
            if "company_name" not in parsed and "name" in parsed:
                parsed["company_name"] = parsed.pop("name")
            s = CompanyDataSerializer(data=parsed)
            s.is_valid(raise_exception=True)
            return s.validated_data
        except Exception as e:
            raise serializers.ValidationError(f"company는 JSON 객체여야 합니다: {e}")


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
