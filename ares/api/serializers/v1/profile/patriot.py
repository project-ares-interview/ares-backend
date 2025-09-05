from rest_framework import serializers

from ares.api.models import Patriot


class PatriotSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patriot
        fields = "__all__"
        read_only_fields = ["user"]
