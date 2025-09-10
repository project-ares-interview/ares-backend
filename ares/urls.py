# ares/urls.py
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
# ares/urls.py
from django.contrib import admin
from django.urls import path, include
from .views import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),

    # ✅ 우리 API (이후 2단계에서 ares/api/urls.py 생성)
    path("api/", include("ares.api.urls")),

    # ✅ 인증/회원가입 (dj-rest-auth + allauth)
    path("auth/", include("dj_rest_auth.urls")),
    path("auth/registration/", include("dj_rest_auth.registration.urls")),

    # ✅ 헬스체크 (선택: 슬래시 일관화)
    path("health/", HealthCheckView.as_view(), name="health_check"),
]
