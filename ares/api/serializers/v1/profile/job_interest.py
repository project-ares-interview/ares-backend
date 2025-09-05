from rest_framework import serializers

from ares.api.models import JobInterest


class JobInterestSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(
        required=False,
        help_text="항목의 순서를 나타냅니다. 생성 시에는 자동으로 가장 마지막 순서로 지정됩니다.",
    )

    class Meta:
        model = JobInterest
        fields = "__all__"
        read_only_fields = ["user"]
