from rest_framework import serializers

from ares.api.models.resume.career import ResumeCareer


class ResumeCareerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResumeCareer
        fields = "__all__"
        read_only_fields = ["resume"]
