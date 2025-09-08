from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.profile import Career
from ares.api.serializers.v1.profile.career import CareerSerializer


class CareerViewSet(viewsets.ModelViewSet):
    serializer_class = CareerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Career.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
