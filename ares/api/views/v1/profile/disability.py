from django.db.models import Max
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.profile import Disability
from ares.api.serializers.v1.profile.disability import DisabilitySerializer


class DisabilityViewSet(viewsets.ModelViewSet):
    serializer_class = DisabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Disability.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        queryset = self.get_queryset()
        max_order = queryset.aggregate(Max("order"))["order__max"]
        next_order = max_order + 1 if max_order is not None else 0
        serializer.save(user=self.request.user, order=next_order)
