"""
URL configuration for ares project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from ares.api.views.v1.calendar import HealthCheckView
# from . import views

urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("ares.api.urls")),
    path("health", HealthCheckView.as_view(), name="health_check"),
    # path('authorize/', views.authorize, name='authorize'), # This is handled in api/urls.py

    # path('oauth2callback/', views.oauth2callback, name='oauth2callback'),
    
    # # [4단계를 위한 URL 패턴들]
    # path('calendar/', views.calendar_view, name='calendar'),
    # path('add_event/', views.add_event, name='add_event'),
    #     # [Google 인증을 위한 URL]
    # # 프론트엔드가 'Google 연동' 버튼에 연결할 주소
    # path('google/authorize/', views.authorize, name='authorize'),
    # # Google Cloud Console에 등록한 리디렉션 URI
    # path('google/callback/', views.oauth2callback, name='oauth2callback'),
]
