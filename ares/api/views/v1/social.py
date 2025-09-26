from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from django.core.signing import BadSignature, Signer
from rest_framework import status
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ares.api.adapter import NewSocialUserException
from ares.api.serializers.v1.user import SocialUserRegisterSerializer
from ares.settings import CLIENT_HOST, CLIENT_PORT


class CustomSocialLoginView(SocialLoginView):
    def post(self, request, *args, **kwargs):
        self.request = request
        self.serializer = self.get_serializer(data=self.request.data)

        try:
            self.serializer.is_valid(raise_exception=True)
        except NewSocialUserException as e:
            sociallogin = e.sociallogin
            # New user: return signed social data for registration
            signer = Signer()
            signed_data = signer.sign_object({
                "email": sociallogin.user.email,
                "name": sociallogin.user.name or "",
            })
            response_data = {
                "status": "registration_required",
                "signed_data": signed_data,
                "email": sociallogin.user.email,
                "name": sociallogin.user.name or "",
            }
            return Response(response_data, status=status.HTTP_200_OK)

        # Existing user: log them in
        return super(SocialLoginView, self).post(request, *args, **kwargs)


class GoogleLogin(CustomSocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client


class GoogleRegisterView(CreateAPIView):
    serializer_class = SocialUserRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        signer = Signer()
        signed_data = self.request.data.get("signed_data")
        
        if not signed_data:
            # This should ideally be caught by a custom validation in the serializer
            return
            
        try:
            social_data = signer.unsign_object(signed_data)
        except BadSignature:
            # This should also be caught by validation
            return

        # Pass the validated social data to the save method
        # This will override the read_only fields
        serializer.save(
            email=social_data.get("email"),
            name=self.request.data.get("name")
        )
