from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models import Patriot
from ares.api.serializers.v1.profile.patriot import PatriotSerializer


class PatriotViewSet(viewsets.ModelViewSet):
    serializer_class = PatriotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Patriot.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
