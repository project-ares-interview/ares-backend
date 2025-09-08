from rest_framework import serializers

from ares.api.models.resume.language import ResumeLanguage


class ResumeLanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResumeLanguage
        fields = "__all__"
        read_only_fields = ["resume"]
