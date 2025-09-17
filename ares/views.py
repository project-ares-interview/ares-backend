from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from ares.api.models.calendar import GoogleAuthToken
import os

# 🌟 [2단계에서 설치한 라이브러리들을 import 합니다]
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


class HealthCheckView(APIView):
    """
    API server health check
    """

    def get(self, request, *args, **kwargs):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


# 🌟 [Google Calendar API의 권한 범위를 정의합니다]
# 'calendar'는 읽기와 쓰기 권한을 모두 포함합니다.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
]


@login_required
def get_google_creds(request):
    """현재 로그인한 사용자의 토큰을 DB에서 가져와 유효성을 검사하고 반환합니다."""
    try:
        token_model = request.user.googleauthtoken
        creds = token_model.to_credentials()
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GoogleAuthToken.from_credentials(request.user, creds)
        return creds
    except GoogleAuthToken.DoesNotExist:
        return None


# --- [메인 뷰] ---
@login_required
def calendar_view(request):
    """캘린더 메인 페이지. 토큰 유무를 확인하여 적절한 행동을 취합니다."""
    creds = get_google_creds(request)
    if not creds:
        # [흐름 1] 토큰 없음 -> 인증 시작 페이지로 보낸다.
        return redirect("authorize")

    # [흐름 4] 토큰 있음 -> 일정 등록 폼이 있는 페이지를 보여준다.
    return render(request, "calendar.html")


# --- [인증 관련 뷰] ---
@login_required
def authorize(request):
    """
    [흐름 2] Google 인증 페이지로 사용자를 보냅니다.
    .env 파일에 저장된 '대표 초대장' 정보를 사용합니다.
    """
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(client_config=client_config, scopes=SCOPES)
    flow.redirect_uri = request.build_absolute_uri(reverse("oauth2callback"))
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # 항상 동의 화면을 표시하여 사용자가 계정을 선택할 수 있게 함
    )
    request.session["state"] = state
    return redirect(authorization_url)


@login_required
def oauth2callback(request):
    """
    [흐름 3] Google에서 돌아온 사용자의 토큰을 '개인 보관함(DB)'에 저장합니다.
    """
    state = request.session.pop("state", "")  # pop으로 한번 사용 후 제거
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config=client_config, scopes=SCOPES, state=state
    )
    flow.redirect_uri = request.build_absolute_uri(reverse("oauth2callback"))

    authorization_response = request.build_absolute_uri()
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials

    # [핵심!] 토큰을 파일이 아닌, 현재 로그인한 사용자(request.user)와 연결하여 DB에 저장
    GoogleAuthToken.from_credentials(request.user, creds)

    return redirect("calendar")


# --- [기능 실행 뷰] ---
@login_required
def add_event(request):
    """DB에 저장된 개인 토큰을 사용하여 Google Calendar에 일정을 추가합니다."""
    if request.method == "POST":
        creds = get_google_creds(request)
        if not creds:
            return redirect("authorize")

        try:
            service = build("calendar", "v3", credentials=creds)
            event_data = {
                "summary": request.POST.get("summary"),
                "description": request.POST.get("description"),
                "start": {
                    "dateTime": f"{request.POST.get('start_time')}:00",
                    "timeZone": "Asia/Seoul",
                },
                "end": {
                    "dateTime": f"{request.POST.get('end_time')}:00",
                    "timeZone": "Asia/Seoul",
                },
            }
            service.events().insert(calendarId="primary", body=event_data).execute()
        except Exception as e:
            print(f"🚨 이벤트 생성 오류: {e}")
            # 여기서 사용자에게 오류 메시지를 보여주는 로직을 추가할 수 있습니다.

    return redirect("calendar")
