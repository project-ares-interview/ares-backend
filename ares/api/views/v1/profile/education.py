from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ares.api.models.profile import Education
from ares.api.serializers.v1.profile.education import EducationSerializer


class EducationViewSet(viewsets.ModelViewSet):
    serializer_class = EducationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Education.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
