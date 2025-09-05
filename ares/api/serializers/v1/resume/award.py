from rest_framework import serializers

from ares.api.models.resume.award import ResumeAward


class ResumeAwardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResumeAward
        fields = "__all__"
        read_only_fields = ["resume"]
