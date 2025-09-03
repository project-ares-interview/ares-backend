from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin

from ares.api.models import User


class NewSocialUserException(Exception):
    def __init__(self, sociallogin: SocialLogin):
        self.sociallogin = sociallogin
        super().__init__("New social user")


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        # This hook is called after the user has successfully authenticated with
        # the social provider, but before the login is processed.

        # Check if the social account already exists
        if sociallogin.is_existing:
            return

        # Check if a user with this email already exists
        try:
            user = User.objects.get(email=sociallogin.user.email)
            # If so, connect the social account to the existing user
            sociallogin.connect(request, user)
        except User.DoesNotExist:
            # If not, raise our custom exception to signal that this is a new user
            # who needs to go through the registration flow.
            raise NewSocialUserException(sociallogin)
