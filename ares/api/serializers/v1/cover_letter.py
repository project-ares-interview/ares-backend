from rest_framework import serializers

from ares.api.models.cover_letter import CoverLetter


class CoverLetterSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoverLetter
        fields = "__all__"
        read_only_fields = ["user", "created_at"]
