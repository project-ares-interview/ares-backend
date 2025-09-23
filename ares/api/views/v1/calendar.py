import os
import datetime
from django.shortcuts import redirect
from django.urls import reverse
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.conf import settings
from urllib.parse import urlparse
import logging
from django.contrib.auth import get_user_model
from django.core.signing import Signer, BadSignature
from rest_framework.decorators import api_view, permission_classes # 🌟 DRF 데코레이터 사용
from rest_framework.permissions import IsAuthenticated           # 🌟 DRF 인증 사용
from rest_framework.response import Response
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from ares.api.models.calendar import GoogleAuthToken # 🌟 calendar.py에서 모델을 가져옵니다.
from django.utils import timezone 
from rest_framework import permissions

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

logger = logging.getLogger(__name__)

def get_client_config():
    """환경 변수에서 클라이언트 설정을 읽어오는 헬퍼 함수."""
    return { "web": { "client_id": os.getenv("GOOGLE_CLIENT_ID"), "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"), "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token" } }

# --- [API 엔드포인트 뷰] ---
EVENT_TAG = "[ARES_JOB]"


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendar_view(request):
    """
    [최종 업그레이드 버전]
    '비밀 표식'이 있는 캘린더 일정만 필터링하고,
    날짜/시간을 '깔끔한 형식'으로 가공하여 반환합니다.
    """
    try:
        token_model = request.user.googleauthtoken
        creds = token_model.to_credentials()

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GoogleAuthToken.from_credentials(request.user, creds)
        
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary', 
            timeMin=now,
            q=EVENT_TAG, 
            maxResults=50, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        raw_events = events_result.get('items', [])
        
        processed_events = []
        try:
            service = build('calendar', 'v3', credentials=creds)
            
            # 1. 검색 시작 기준: '바로 지금' (지나간 일정은 제외)
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            
            events_result = service.events().list(
                calendarId='primary', 
                timeMin=now,
                q=EVENT_TAG, # 👈 [ARES_JOB] 태그가 있는 일정만 가져옵니다.
                maxResults=100, 
                singleEvents=True,
                # Google에게는 일단 시작 시간 순으로 1차 정렬을 요청합니다.
                orderBy='startTime'
            ).execute()
            
            raw_events = events_result.get('items', [])
            
            for event in raw_events:
                start_info = event.get('start', {})
                end_info = event.get('end', {})
                
                start_str_raw = start_info.get('dateTime', start_info.get('date'))
                end_str_raw = end_info.get('dateTime', end_info.get('date'))

                # 2. 날짜/시간을 깔끔한 형식으로 가공합니다.
                start_display = "정보 없음"
                if start_str_raw:
                    # ... (이전 답변의 날짜/시간 가공 로직과 동일) ...
                    if 'T' in start_str_raw:
                        dt_obj = datetime.datetime.fromisoformat(start_str_raw.replace('Z', '+00:00'))
                        start_display = dt_obj.astimezone(timezone.get_default_timezone()).strftime('%Y-%m-%d %H:%M')
                    else:
                        start_display = start_str_raw
                
                end_display = "정보 없음"
                if end_str_raw:
                    if 'T' in end_str_raw:
                        dt_obj = datetime.datetime.fromisoformat(end_str_raw.replace('Z', '+00:00'))
                        end_display = dt_obj.astimezone(timezone.get_default_timezone()).strftime('%Y-%m-%d %H:%M')
                    else:
                        end_display = end_str_raw
                
                processed_events.append({
                    'id': event.get('id'),
                    'summary': event.get('summary', '').replace(EVENT_TAG, '').strip(),
                    'description': event.get('description', ''),
                    'start': start_display,
                    'end': end_display,
                    'raw_end': end_str_raw, # 👈 정렬을 위한 원본 마감일 데이터
                })

        except Exception as e:
            return Response({'error': f'캘린더 로딩 중 오류 발생: {str(e)}'}, status=500)

        # 3. <<<<<<< 🌟 [핵심] '마감일(raw_end)' 순서로 최종 정렬합니다 >>>>>>>>>
        processed_events.sort(key=lambda x: x.get('raw_end') or '')
        
        return Response({
            'status': 'authenticated',
            'events': processed_events
        })

    except GoogleAuthToken.DoesNotExist:
        authorization_url = request.build_absolute_uri(reverse('api:get_google_auth_url'))
        return Response({
            'status': 'google_auth_required',
            'authorization_url': authorization_url
        })
    except Exception as e:
        return Response({'error': f'캘린더 로딩 중 오류 발생: {str(e)}'}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_event(request):
    """[API] 캘린더에 새 일정을 추가할 때, 제목에 '비밀 표식'을 붙입니다."""
    try:
        token_model = request.user.googleauthtoken
        creds = token_model.to_credentials()
        # (토큰 유효성 검사 추가 권장)

        service = build('calendar', 'v3', credentials=creds)

        data = request.data
        start_time_str = data.get('start_time', '')
        end_time_str = data.get('end_time', '')

        if not start_time_str or not end_time_str:
            return Response({'error': '시작 날짜와 종료 날짜는 필수입니다.'}, status=400)
            
        event_data = {
            'summary': f"{EVENT_TAG} {data.get('summary')}",
            'description': data.get('description'),
        }

        # <<<<<<< 🌟 [핵심] 날짜 형식에 따라 '하루 종일' 또는 '시간 지정' 일정을 만듭니다 >>>>>>>>>
        
        # [분기 1] 사용자가 시간 없이 날짜만 입력한 경우 (예: "2025-09-20")
        if 'T' not in start_time_str and ':' not in start_time_str:
            # 하루 종일 일정으로 처리합니다.
            event_data['start'] = {'date': start_time_str}
            
            # 종료일은 +1일을 해야 Google Calendar에서 올바르게 표시됩니다.
            end_date_obj = datetime.datetime.strptime(end_time_str, '%Y-%m-%d').date()
            end_date_exclusive = end_date_obj + datetime.timedelta(days=1)
            event_data['end'] = {'date': end_date_exclusive.strftime('%Y-%m-%d')}
        
        # [분기 2] 사용자가 시간까지 입력한 경우 (예: "2025-09-20T10:00")
        else:
            # 기존의 시간 지정 일정으로 처리합니다.
            start_iso = datetime.datetime.fromisoformat(start_time_str.replace(' ', 'T')).isoformat()
            end_iso = datetime.datetime.fromisoformat(end_time_str.replace(' ', 'T')).isoformat()
            event_data['start'] = {'dateTime': start_iso, 'timeZone': 'Asia/Seoul'}
            event_data['end'] = {'dateTime': end_iso, 'timeZone': 'Asia/Seoul'}

        service.events().insert(calendarId='primary', body=event_data).execute()
        return Response({
            'status': 'success',
            'message': '일정이 성공적으로 추가되었습니다.'
        })
        
    except Exception as e:
        return Response({'error': f'이벤트 생성 중 오류 발생: {str(e)}'}, status=400)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_event(request, event_id):
    """[API 메뉴 3] 캘린더에서 특정 이벤트를 삭제합니다."""
    try:
        token_model = request.user.googleauthtoken
        creds = token_model.to_credentials()

        service = build('calendar', 'v3', credentials=creds)
        
        # Google Calendar API를 호출하여 이벤트를 삭제합니다.
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        # 성공적으로 삭제되면 메시지와 함께 200 OK 응답을 보냅니다.
        return Response({
            'status': 'success',
            'message': '삭제되었습니다.'
        })

    except GoogleAuthToken.DoesNotExist:
        return JsonResponse({'error': 'Google 인증이 필요합니다.'}, status=401)
    except Exception as e:
        # eventId가 잘못되었거나 다른 API 오류일 수 있습니다.
        return JsonResponse({'error': f'이벤트 삭제 중 오류 발생: {str(e)}'}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def authorize(request):
    """사용자를 Google 인증 페이지로 보냅니다."""
    flow = Flow.from_client_config(client_config=get_client_config(), scopes=SCOPES)
    flow.redirect_uri = request.build_absolute_uri(reverse('api:oauth2callback'))
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    request.session['state'] = state
    # 🌟 이제 URL을 JSON으로 반환하는 대신, 그냥 바로 리디렉션합니다.
    #    프론트엔드에서는 a 태그나 window.location.href로 이 주소를 호출합니다.
    return redirect(authorization_url)


def oauth2callback(request):
    """Google이 호출하는 콜백. 로그인 없이 state로 사용자 식별 후 토큰 저장 및 안전 리다이렉트."""
    try:
        logger.info("[oauth2callback] called", extra={
            'path': request.get_full_path(),
            'query_params': dict(request.GET.items()),
        })
        # 1) 반드시 쿼리의 state를 사용 (세션 state와 불일치 시 oauthlib가 mismatch 발생)
        state = request.GET.get('state', '')
        if not state:
            logger.warning("[oauth2callback] missing state")
            return redirect('/calendar?google_auth_status=error&reason=missing_state')

        # 2) state 서명 검증 및 사용자 식별
        signer = Signer()
        try:
            user_id_str = signer.unsign(state)
            logger.info("[oauth2callback] state verified", extra={'user_id': user_id_str})
        except BadSignature:
            logger.warning("[oauth2callback] invalid state signature", extra={'state': state})
            return redirect('/calendar?google_auth_status=error&reason=invalid_state')

        User = get_user_model()
        try:
            user = User.objects.get(id=user_id_str)
            logger.info("[oauth2callback] user loaded", extra={'user_id': user.id})
        except User.DoesNotExist:
            logger.warning("[oauth2callback] user not found", extra={'user_id': user_id_str})
            return redirect('/calendar?google_auth_status=error&reason=user_not_found')

        # 3) 동일 state로 Flow 복원 및 토큰 교환
        flow = Flow.from_client_config(client_config=get_client_config(), scopes=SCOPES, state=state)
        flow.redirect_uri = request.build_absolute_uri(reverse('api:oauth2callback'))
        logger.info("[oauth2callback] redirect_uri built", extra={'redirect_uri': flow.redirect_uri})
        authorization_response = request.build_absolute_uri()
        logger.info("[oauth2callback] authorization_response", extra={'authorization_response': authorization_response})
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        logger.info("[oauth2callback] token fetched")

        # 4) 사용자별 토큰 저장
        GoogleAuthToken.from_credentials(user, creds)
        logger.info("[oauth2callback] token saved", extra={'user_id': user.id})

        # 5) 안전 리다이렉트 (세션 → GET 쿼리 → 기본값)
        has_in_session = 'oauth_return_url' in request.session
        session_key = getattr(request.session, 'session_key', None)
        cookies = dict(request.COOKIES)
        return_url = request.session.pop('oauth_return_url', None) or request.GET.get('return_url') or '/calendar'
        logger.info(
            "[oauth2callback] return_url resolved has_in_session=%s session_key=%s return_url=%s cookies=%s",
            str(has_in_session), str(session_key), str(return_url), str(cookies)
        )
        return redirect(return_url)
    except Exception as e:
        logger.exception("[oauth2callback] unexpected error: %s", e)
        return redirect('/calendar?google_auth_status=error')


# =============================================================================
# 프론트엔드 명세에 맞는 새로운 엔드포인트들
# =============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_events(request):
    """이벤트 목록 조회 - 프론트엔드 명세에 맞춘 응답 형식"""
    try:
        # GoogleAuthToken이 존재하는지 먼저 확인
        try:
            token_model = request.user.googleauthtoken
        except GoogleAuthToken.DoesNotExist:
            return Response({
                'status': 'google_auth_required',
                'message': 'Google 인증이 필요합니다.'
            })
        
        creds = token_model.to_credentials()

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GoogleAuthToken.from_credentials(request.user, creds)
        
        service = build('calendar', 'v3', credentials=creds)
        
        # 현재 시간부터 이벤트 조회
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary', 
            timeMin=now,
            q=EVENT_TAG,  # [ARES_JOB] 태그가 있는 일정만 가져옵니다.
            maxResults=100, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        raw_events = events_result.get('items', [])
        
        events = []
        
        for event in raw_events:
            start_info = event.get('start', {})
            end_info = event.get('end', {})
            
            start_str_raw = start_info.get('dateTime', start_info.get('date'))
            end_str_raw = end_info.get('dateTime', end_info.get('date'))

            # 날짜/시간을 ISO 형식으로 변환
            start_display = start_str_raw if start_str_raw else "정보 없음"
            end_display = end_str_raw if end_str_raw else "정보 없음"
            
            events.append({
                'id': event.get('id'),
                'summary': event.get('summary', '').replace(EVENT_TAG, '').strip(),
                'description': event.get('description', ''),
                'start': start_display,
                'end': end_display,
            })

        return Response({
            'status': 'authenticated',
            'events': events
        })

    except Exception as e:
        return Response({
            'status': 'error',
            'message': f'이벤트 조회 중 오류 발생: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_google_auth_url(request):
    """Google OAuth URL 생성 - 프론트엔드에서 사용할 URL 반환"""
    try:
        flow = Flow.from_client_config(client_config=get_client_config(), scopes=SCOPES)
        flow.redirect_uri = request.build_absolute_uri(reverse('api:oauth2callback'))

        # 사용자 식별 정보를 안전하게 state에 서명하여 담습니다 (백엔드 콜백용)
        signer = Signer()
        user_state = signer.sign(str(request.user.id))

        # 현재 페이지로 돌아가기 위한 return_url 저장 (없으면 기본 경로)
        return_url = request.GET.get('return_url')
        _session_key = getattr(request.session, 'session_key', None)
        _session_keys_before = list(request.session.keys())
        _cookies_snapshot = dict(request.COOKIES)
        logger.info(
            "[get_google_auth_url] received return_url=%s session_key=%s session_keys_before=%s cookies=%s",
            str(return_url), str(_session_key), str(_session_keys_before), str(_cookies_snapshot)
        )

        # 요청받은 값을 그대로 사용 (정상성 검증은 콜백 단계에서 수행)
        request.session['oauth_return_url'] = return_url
        _session_key_after = getattr(request.session, 'session_key', None)
        _has_oauth_return_url = 'oauth_return_url' in request.session
        _session_keys_after = list(request.session.keys())
        logger.info(
            "[get_google_auth_url] stored session_key=%s has_oauth_return_url=%s session_keys_after=%s",
            str(_session_key_after), str(_has_oauth_return_url), str(_session_keys_after)
        )

        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
            state=user_state
        )
        # 필요 시 검증 대비로도 보관 가능하지만, 모바일/웹 콜백에서 쿠키가 없을 수 있으므로 state 자체에 담습니다
        request.session['state'] = state
        
        return Response({
            'authorization_url': authorization_url,
            'state': state
        })
    except Exception as e:
        return Response({
            'status': 'error',
            'message': f'Google 인증 URL 생성 중 오류 발생: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def handle_google_auth_callback(request):
    """Google OAuth 콜백 처리 - 프론트엔드에서 code와 state를 받아서 처리"""
    try:
        code = request.data.get('code')
        state = request.data.get('state')
        
        if not code:
            return Response({
                'status': 'error',
                'message': 'Authorization code가 필요합니다.'
            }, status=400)

        # state에서 사용자 식별 복원
        signer = Signer()
        try:
            user_id_str = signer.unsign(state)
        except BadSignature:
            return Response({
                'status': 'error',
                'message': 'Invalid state parameter'
            }, status=400)
        User = get_user_model()
        try:
            user = User.objects.get(id=user_id_str)
        except User.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Invalid user in state'
            }, status=400)
        
        flow = Flow.from_client_config(client_config=get_client_config(), scopes=SCOPES, state=state)
        flow.redirect_uri = request.build_absolute_uri(reverse('api:oauth2callback'))
        
        # Authorization code를 사용해서 토큰 교환
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        # 토큰을 데이터베이스에 저장
        GoogleAuthToken.from_credentials(user, creds)
        
        # 세션에서 state 제거
        request.session.pop('state', '')
        
        # 안전 리다이렉트: 세션 → 요청 바디 → 기본값 순으로 결정
        return_url = request.session.pop('oauth_return_url', None) or request.data.get('return_url')
        return HttpResponseRedirect(return_url)
        
    except Exception as e:
        return Response({
            'status': 'error',
            'message': f'OAuth 콜백 처리 중 오류 발생: {str(e)}'
        }, status=500)