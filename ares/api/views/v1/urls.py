from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from ares.api.views.v1.example import ExampleViewSet
from ares.api.views.v1.social import GoogleLogin, GoogleRegisterView
from ares.api.views.v1.user import UserDetailView, UserRegisterView
from dj_rest_auth.views import LoginView, LogoutView

router = DefaultRouter()
router.register(r"examples", ExampleViewSet, basename="example")

urlpatterns = [
    # Router URLs
    path("", include(router.urls)),
    path("user/", UserDetailView.as_view(), name="user_detail"),

    # Custom Views
    path("auth/registration/", UserRegisterView.as_view(), name="rest_register"),
    path("auth/user/", UserDetailView.as_view(), name="user_detail"),
    
    # dj-rest-auth Views
    path("auth/login/", LoginView.as_view(), name="rest_login"),
    path("auth/logout/", LogoutView.as_view(), name="rest_logout"),
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(),
        name="token_refresh",
    ),

    # Social Auth
    path(
        "auth/google/",
        GoogleLogin.as_view(),
        name="google_login",
    ),
    path(
        "auth/google/register/",
        GoogleRegisterView.as_view(),
        name="google_register",
    ),
]
