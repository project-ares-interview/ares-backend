from rest_framework import serializers

from ares.api.models.resume.education import ResumeEducation


class ResumeEducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResumeEducation
        fields = "__all__"
        read_only_fields = ["resume"]
