from django.db import models
from django.conf import settings
from google.oauth2.credentials import Credentials 

class GoogleAuthToken(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255, null=True)
    token_uri = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    scopes = models.TextField()

    def to_credentials(self):
        """DB 정보를 다시 Credentials 객체로 변환합니다."""
        return Credentials(
            token=self.token, refresh_token=self.refresh_token,
            token_uri=self.token_uri, client_id=self.client_id,
            client_secret=self.client_secret, scopes=self.scopes.split(' ')
        )

    @classmethod
    def from_credentials(cls, user, creds):
        """Credentials 객체를 받아 DB에 저장/업데이트합니다."""
        cls.objects.update_or_create(
            user=user,
            defaults={
                'token': creds.token, 'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri, 'client_id': creds.client_id,
                'client_secret': creds.client_secret, 'scopes': ' '.join(creds.scopes)
            }
        )