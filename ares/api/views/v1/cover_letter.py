from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.cover_letter import CoverLetter
from ares.api.serializers.v1.cover_letter import (
    CoverLetterSerializer,
)


class CoverLetterViewSet(viewsets.ModelViewSet):
    serializer_class = CoverLetterSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CoverLetter.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
