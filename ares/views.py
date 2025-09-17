from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, get_user_model
from ares.api.models.calendar import GoogleAuthToken
import os
import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


from drf_spectacular.utils import extend_schema
from rest_framework import serializers


class HealthCheckSerializer(serializers.Serializer):
    status = serializers.CharField()


class HealthCheckView(APIView):
    """API server health check"""

    @extend_schema(responses=HealthCheckSerializer)
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


def get_client_config():
    """환경 변수에서 클라이언트 설정을 읽어오는 헬퍼 함수 (코드 중복 제거)"""
    return {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


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


def authorize(request):
    """사용자를 Google 인증 페이지로 보냅니다."""
    flow = Flow.from_client_config(client_config=get_client_config(), scopes=SCOPES)
    flow.redirect_uri = request.build_absolute_uri(reverse("oauth2callback"))
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    request.session["state"] = state
    return redirect(authorization_url)


def oauth2callback(request):
    """Google에서 돌아온 사용자의 토큰을 DB에 저장하고 Django에 로그인합니다."""
    state = request.session.pop("state", "")
    flow = Flow.from_client_config(
        client_config=get_client_config(), scopes=SCOPES, state=state
    )
    flow.redirect_uri = request.build_absolute_uri(reverse("oauth2callback"))

    authorization_response = request.build_absolute_uri()
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials

    # Google 사용자 정보 조회 후 사용자 생성/로그인
    try:
        userinfo_service = build("oauth2", "v2", credentials=creds)
        user_info = userinfo_service.userinfo().get().execute()
        email = user_info.get("email")
        display_name = user_info.get("name") or email
        if not email:
            return redirect("/")

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            email=email, defaults={"username": email, "name": display_name}
        )
        login(request, user)
        GoogleAuthToken.from_credentials(user, creds)
    except Exception:
        # 사용자 정보 조회 실패 시에도 최소한 토큰만 저장 시도 (로그인 필요)
        if request.user and request.user.is_authenticated:
            GoogleAuthToken.from_credentials(request.user, creds)

    return redirect("calendar")


def calendar_view(request):
    # 비로그인 사용자는 곧바로 Google 인증으로 보냄
    if not request.user.is_authenticated:
        return redirect("authorize")

    creds = get_google_creds(request)
    if not creds:
        return redirect("authorize")

    processed_events = []
    try:
        service = build("calendar", "v3", credentials=creds)

        now_utc = datetime.datetime.utcnow()

        time_min_utc = now_utc

        time_max_utc = now_utc + datetime.timedelta(days=30)

        time_min_iso = time_min_utc.isoformat() + "Z"
        time_max_iso = time_max_utc.isoformat() + "Z"

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                maxResults=50,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        raw_events = events_result.get("items", [])

        # Python에서 보여주기 좋은 형태로 데이터 가공
        for event in raw_events:
            start_info = event.get("start", {})
            end_info = event.get("end", {})

            start_display = start_info.get("dateTime", start_info.get("date", ""))
            end_display = end_info.get("dateTime", end_info.get("date", ""))

            # 원본 날짜/시간 값도 정렬을 위해 저장
            original_end_time = end_display

            if "T" in start_display:
                start_display = start_display[:16].replace("T", " ")
            if "T" in end_display:
                end_display = end_display[:16].replace("T", " ")

            processed_events.append(
                {
                    "summary": event.get("summary", "제목 없음"),
                    "start": start_display,
                    "end": end_display,
                    "original_end": original_end_time,  # 🌟 정렬의 기준이 될 원본 종료 시간
                }
            )

    except Exception as e:
        print(f"🚨 캘린더 일정 가져오기 오류: {e}")

    # <<<<<<< 🌟 [핵심] Python에서 '마감일(end)' 순서로 최종 정렬합니다 >>>>>>>>>
    # lambda 함수를 사용하여 각 딕셔너리의 'original_end' 값을 기준으로 리스트를 정렬합니다.
    processed_events.sort(key=lambda x: x["original_end"])

    return render(request, "calendar.html", {"events": processed_events})


@login_required
def add_event(request):
    if request.method != "POST":
        return redirect("calendar")

    creds = get_google_creds(request)
    if not creds:
        return redirect("authorize")

    try:
        service = build("calendar", "v3", credentials=creds)

        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")

        start_datetime_obj = datetime.datetime.fromisoformat(start_time_str)
        end_datetime_obj = datetime.datetime.fromisoformat(end_time_str)

        start_iso_format = start_datetime_obj.isoformat()
        end_iso_format = end_datetime_obj.isoformat()

        event_data = {
            "summary": request.POST.get("summary"),
            "description": request.POST.get("description"),
            "start": {
                "dateTime": start_iso_format,
                "timeZone": "Asia/Seoul",
            },
            "end": {
                "dateTime": end_iso_format,
                "timeZone": "Asia/Seoul",
            },
        }
        service.events().insert(calendarId="primary", body=event_data).execute()
        print("✅ 이벤트 생성 성공!")
    except Exception as e:
        # 오류가 발생했을 때, 어떤 데이터로 요청했는지 확인하면 디버깅에 큰 도움이 됩니다.
        print(f"🚨 이벤트 생성 오류: {e}")
        print(f"--- 전송 시도 데이터 ---")
        print(f"Start Time String: {start_time_str}")
        print(f"End Time String: {end_time_str}")
        print(f"Formatted Event Data: {event_data}")

    return redirect("calendar")
