from rest_framework import serializers

from ares.api.models.resume.base import Resume


class ResumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resume
        fields = ["id", "user", "title", "created_at", "updated_at"]
        read_only_fields = ["user", "created_at", "updated_at"]
