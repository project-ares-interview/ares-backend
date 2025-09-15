from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse
import os.path
import datetime

# 🌟 [2단계에서 설치한 라이브러리들을 import 합니다]
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

class HealthCheckView(APIView):
    """
    API server health check
    """

    def get(self, request, *args, **kwargs):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


# 🌟 [Google Calendar API의 권한 범위를 정의합니다]
# 'calendar'는 읽기와 쓰기 권한을 모두 포함합니다.
SCOPES = ['https://www.googleapis.com/auth/calendar']


def get_google_creds():
    """
    token.json 파일을 확인하여 유효한 Google 자격 증명을 반환하거나,
    없으면 None을 반환하는 헬퍼 함수입니다.
    """
    creds = None
    # token.json 파일은 사용자가 성공적으로 로그인하면 생성됩니다.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # 자격 증명이 없거나 유효하지 않은 경우
    if not creds or not creds.valid:
        # 자격 증명이 만료되었고, 재발급 토큰이 있는 경우
        if creds and creds.expired and creds.refresh_token:
            # 토큰을 새로고침(재발급)합니다.
            creds.refresh(Request())
            # 재발급된 토큰을 다시 파일에 저장하여 다음번엔 재로그인할 필요가 없도록 합니다.
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        else:
            # 토큰이 아예 없거나, 재발급도 불가능하면 None을 반환하여
            # 사용자가 새로 로그인해야 함을 알립니다.
            return None
    return creds


def authorize(request):
    """
    [여행의 시작]
    사용자를 Google 인증 페이지로 보내는 역할을 합니다.
    """
    # 1. Google Cloud에서 다운로드한 '초대장(credentials.json)'을 기반으로 인증 절차를 시작합니다.
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    
    # 2. Google이 인증을 마친 후 사용자를 돌려보낼 우리 웹사이트의 주소(리디렉션 URI)를 알려줍니다.
    #    request.build_absolute_uri()는 현재 도메인을 포함한 전체 URL을 만들어줍니다. (예: http://127.0.0.1:8000/oauth2callback)
    flow.redirect_uri = request.build_absolute_uri(reverse('oauth2callback'))

    # 3. 사용자를 보낼 최종 인증 URL을 생성합니다.
    #    access_type='offline'은 나중에도 계속 API를 사용할 수 있도록 '재발급 토큰'을 요청하는 중요한 옵션입니다.
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    
    # 4. CSRF 공격을 방지하기 위해 state 값을 세션에 저장합니다.
    request.session['state'] = state
    
    # 5. 최종적으로 사용자를 생성된 Google 인증 URL로 보냅니다. (페이지 이동)
    return redirect(authorization_url)


def oauth2callback(request):
    """
    [여행의 끝]
    Google에서 인증을 마치고 돌아온 사용자를 맞는 역할을 합니다.
    """
    # 1. 이전 단계에서 세션에 저장했던 state 값을 가져와서, Google이 보낸 state와 일치하는지 확인합니다.
    state = request.session['state']
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES, state=state)
    flow.redirect_uri = request.build_absolute_uri(reverse('oauth2callback'))

    # 2. 현재 URL에 포함된 인증 코드를 사용하여, Google 서버와 통신하여 최종 '신용카드(토큰)'를 발급받습니다.
    authorization_response = request.build_absolute_uri()
    flow.fetch_token(authorization_response=authorization_response)

    # 3. 발급받은 '신용카드(토큰)' 정보를 creds 변수에 저장합니다.
    creds = flow.credentials
    
    # 4. 🌟 [가장 중요한 부분] 앞으로 계속 사용하기 위해, 이 '신용카드(토큰)' 정보를
    #    'token.json'이라는 이름의 파일로 프로젝트 폴더에 저장합니다.
    with open('token.json', 'w') as token:
        token.write(creds.to_json())
    
    # 5. 모든 인증 절차가 끝났으므로, 사용자를 원래의 캘린더 페이지로 다시 보냅니다.
    return redirect(reverse('calendar')) # 'calendar'는 캘린더를 보여줄 URL의 이름입니다.

# [당신의 Django 앱 이름]/views.py (3단계 코드에 이어서 추가)

def calendar_view(request):
    """
    [기능 1: 메인 페이지 보여주기]
    사용자가 로그인했는지 확인하고, '일정 등록 폼'이 있는 메인 페이지를 보여줍니다.
    """
    # 1. 헬퍼 함수를 이용해 유효한 '만능 열쇠(creds)'가 있는지 확인합니다.
    creds = get_google_creds()
    
    # 2. 만능 열쇠가 없다면 (로그인한 적이 없다면),
    #    3단계에서 만들었던 '인증 여행'을 시작하도록 authorize 페이지로 보냅니다.
    if not creds:
        return redirect('authorize') # 'authorize'는 urls.py에 정의된 이름

    # 3. 만능 열쇠가 있다면 (로그인 성공),
    #    '일정 등록 폼'이 있는 calendar.html 페이지를 사용자에게 보여줍니다.
    return render(request, 'calendar.html')


def add_event(request):
    """
    [기능 2: 실제 일정 추가하기]
    사용자가 폼에 내용을 입력하고 '추가' 버튼을 누르면 이 함수가 실행됩니다.
    """
    # 1. POST 요청일 때만 (폼 제출일 때만) 아래 로직을 실행합니다.
    if request.method == 'POST':
        
        # 2. 유효한 '만능 열쇠(creds)'를 가져옵니다.
        #    이 시점에는 이미 인증이 끝났으므로, 거의 항상 성공합니다.
        creds = get_google_creds()
        if not creds:
            return redirect('authorize') # 혹시라도 열쇠가 없다면 다시 인증

        try:
            # 3. '만능 열쇠'를 사용하여 Google Calendar 서비스에 연결합니다.
            service = build('calendar', 'v3', credentials=creds)

            # 4. 웹 페이지의 폼(form)에서 사용자가 입력한 데이터들을 가져옵니다.
            summary = request.POST.get('summary')       # 일정 제목
            start_time = request.POST.get('start_time') # 시작 시간 (예: '2025-10-01T10:00')
            end_time = request.POST.get('end_time')     # 종료 시간 (예: '2025-10-01T11:00')
            description = request.POST.get('description') # 설명

            # 5. Google Calendar API가 이해할 수 있는 '요청서(event 딕셔너리)' 형식으로 데이터를 가공합니다.
            event = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': f'{start_time}:00', # 초(second) 정보 추가
                    'timeZone': 'Asia/Seoul',
                },
                'end': {
                    'dateTime': f'{end_time}:00', # 초(second) 정보 추가
                    'timeZone': 'Asia/Seoul',
                },
            }

            # 6. 🌟 [가장 중요한 순간] 가공된 요청서를 Google Calendar 서비스에 보내,
            #    'primary' (기본) 캘린더에 새 일정을 'insert'(추가)하라고 명령합니다.
            created_event = service.events().insert(calendarId='primary', body=event).execute()
            
            print(f"🎉 캘린더 생성 성공! 링크: {created_event.get('htmlLink')}")
            
        except Exception as e:
            print(f"🚨 캘린더 생성 중 오류 발생: {e}")
            # 여기서 사용자에게 오류 메시지를 보여주는 페이지로 이동시킬 수도 있습니다.

        # 7. 일정 추가가 성공하든 실패하든, 다시 메인 캘린더 페이지로 돌아갑니다.
        return redirect('calendar')
    
    # 만약 GET 요청으로 이 주소에 접근했다면, 그냥 메인 페이지로 보냅니다.
    return redirect('calendar')