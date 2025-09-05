from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models import MilitaryService
from ares.api.serializers.v1.profile.military_service import MilitaryServiceSerializer


class MilitaryServiceViewSet(viewsets.ModelViewSet):
    serializer_class = MilitaryServiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return MilitaryService.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
