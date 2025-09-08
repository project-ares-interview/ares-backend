from rest_framework import serializers

from ares.api.models.resume.link import ResumeLink


class ResumeLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResumeLink
        fields = "__all__"
        read_only_fields = ["resume"]
