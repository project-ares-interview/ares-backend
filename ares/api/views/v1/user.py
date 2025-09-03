from rest_framework import generics
from rest_framework.permissions import AllowAny

from ares.api.serializers.v1.user import UserRegisterSerializer
from dj_rest_auth.views import UserDetailsView


class UserRegisterView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]


class UserDetailView(UserDetailsView):
    pass
