from rest_framework import serializers

from ares.api.models import MilitaryService


class MilitaryServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MilitaryService
        fields = "__all__"
        read_only_fields = ["user"]
