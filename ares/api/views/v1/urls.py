from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from ares.api.views.v1.cover_letter import CoverLetterViewSet
from ares.api.views.v1.example import ExampleViewSet
from ares.api.views.v1.profile import (
    CareerViewSet,
    DisabilityViewSet,
    EducationViewSet,
    JobInterestViewSet,
    MilitaryServiceViewSet,
    PatriotViewSet,
)
from ares.api.views.v1.resume.base import ResumeViewSet
from ares.api.views.v1.resume import (
    ResumeAwardViewSet,
    ResumeCareerViewSet,
    ResumeEducationViewSet,
    ResumeLanguageViewSet,
    ResumeLinkViewSet,
)

# ---- Interviews (split views) ----
from ares.api.views.v1.interview.start import InterviewStartAPIView
from ares.api.views.v1.interview.next import InterviewNextQuestionAPIView
from ares.api.views.v1.interview.answer import InterviewSubmitAnswerAPIView
from ares.api.views.v1.interview.finish import InterviewFinishAPIView
from ares.api.views.v1.interview.report import InterviewReportAPIView
from ares.api.views.v1.interview.find_companies import FindCompaniesView
from ares.api.views.v1.interview.coach import interview_coach_view
from ares.api.views.v1.interview.admin import InterviewAdminSyncIndexAPIView  # ← 추가

from ares.api.views.v1.resume_analysis import ResumeAnalysisAPIView
from ares.api.views.v1.social import GoogleLogin, GoogleRegisterView
from ares.api.views.v1.user import UserDetailView, UserRegisterView
from dj_rest_auth.views import LoginView, LogoutView

from ares.api.views.v1.analyze import AnalyzeView, PercentileAnalysisView, GenerateAIAdviceView
from ares.api.views.v1.calendar import calendar_view, add_event, authorize, oauth2callback, delete_event


router = DefaultRouter()
router.register(r"examples", ExampleViewSet, basename="example")

urlpatterns = [
    # Router URLs
    path("", include(router.urls)),

    # ----- AI Analysis -----
    path("resume/analyze/", ResumeAnalysisAPIView.as_view(), name="v1-resume-analyze"),

    # ----- Interviews (AI-based) -----
    path("interviews/start/", InterviewStartAPIView.as_view(), name="v1-interview-start"),
    path("interviews/next/", InterviewNextQuestionAPIView.as_view(), name="v1-interview-next"),
    path("interviews/answer/", InterviewSubmitAnswerAPIView.as_view(), name="v1-interview-answer"),
    path("interviews/finish/", InterviewFinishAPIView.as_view(), name="v1-interview-finish"),
    path("interviews/report/<uuid:session_id>/", InterviewReportAPIView.as_view(), name="v1-interview-report"),
    path("interviews/find-companies/", FindCompaniesView.as_view(), name="interviews-find-companies"),  # ← prefix 통일
    path("interviews/coach/", interview_coach_view, name="interviews-coach"),  # ← prefix 통일
    path("interviews/admin/sync-index/", InterviewAdminSyncIndexAPIView.as_view(), name="v1-interview-admin-sync"),  # ← 추가

    # ----- Cover Letters -----
    path(
        "cover-letters/",
        CoverLetterViewSet.as_view({"get": "list", "post": "create"}),
        name="cover-letter-list",
    ),
    path(
        "cover-letters/<int:pk>/",
        CoverLetterViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="cover-letter-detail",
    ),

    # ----- Resumes (Template) -----
    path(
        "resumes/",
        ResumeViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-list",
    ),
    path(
        "resumes/<int:pk>/",
        ResumeViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-detail",
    ),

    # ----- Resume Nested Resources -----
    path(
        "resumes/<int:resume_pk>/careers/",
        ResumeCareerViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-career-list",
    ),
    path(
        "resumes/<int:resume_pk>/careers/<int:pk>/",
        ResumeCareerViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-career-detail",
    ),
    path(
        "resumes/<int:resume_pk>/educations/",
        ResumeEducationViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-education-list",
    ),
    path(
        "resumes/<int:resume_pk>/educations/<int:pk>/",
        ResumeEducationViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-education-detail",
    ),
    path(
        "resumes/<int:resume_pk>/awards/",
        ResumeAwardViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-award-list",
    ),
    path(
        "resumes/<int:resume_pk>/awards/<int:pk>/",
        ResumeAwardViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-award-detail",
    ),
    path(
        "resumes/<int:resume_pk>/languages/",
        ResumeLanguageViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-language-list",
    ),
    path(
        "resumes/<int:resume_pk>/languages/<int:pk>/",
        ResumeLanguageViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-language-detail",
    ),
    path(
        "resumes/<int:resume_pk>/links/",
        ResumeLinkViewSet.as_view({"get": "list", "post": "create"}),
        name="resume-link-list",
    ),
    path(
        "resumes/<int:resume_pk>/links/<int:pk>/",
        ResumeLinkViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="resume-link-detail",
    ),

    # ----- User Profile -----
    path(
        "profile/military-services/",
        MilitaryServiceViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-military-service-list",
    ),
    path(
        "profile/military-services/<int:pk>/",
        MilitaryServiceViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-military-service-detail",
    ),
    path(
        "profile/patriots/",
        PatriotViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-patriot-list",
    ),
    path(
        "profile/patriots/<int:pk>/",
        PatriotViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-patriot-detail",
    ),
    path(
        "profile/disabilities/",
        DisabilityViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-disability-list",
    ),
    path(
        "profile/disabilities/<int:pk>/",
        DisabilityViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-disability-detail",
    ),
    path(
        "profile/educations/",
        EducationViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-education-list",
    ),
    path(
        "profile/educations/<int:pk>/",
        EducationViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-education-detail",
    ),
    path(
        "profile/careers/",
        CareerViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-career-list",
    ),
    path(
        "profile/careers/<int:pk>/",
        CareerViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-career-detail",
    ),
    path(
        "profile/job-interests/",
        JobInterestViewSet.as_view({"get": "list", "post": "create"}),
        name="profile-job-interest-list",
    ),
    path(
        "profile/job-interests/<int:pk>/",
        JobInterestViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="profile-job-interest-detail",
    ),

    # ----- User / Auth -----
    path("user/", UserDetailView.as_view(), name="user_detail"),
    path("analyze/", AnalyzeView.as_view(), name="analyze"),
    path("analysis/percentiles/", PercentileAnalysisView.as_view(), name="v1-analysis-percentiles"),
    path("analysis/advice/", GenerateAIAdviceView.as_view(), name="v1-analysis-advice"),

    # Custom Views
    path("auth/registration/", UserRegisterView.as_view(), name="rest_register"),
    path("auth/user/", UserDetailView.as_view(), name="auth_user_detail"),  # ← 이름 변경(중복 해소)

    # dj-rest-auth Views
    path("auth/login/", LoginView.as_view(), name="rest_login"),
    path("auth/logout/", LogoutView.as_view(), name="rest_logout"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # Social Auth
    path("auth/google/", GoogleLogin.as_view(), name="google_login"),
    path("auth/google/register/", GoogleRegisterView.as_view(), name="google_register"),

    # ----- Calendar & Google OAuth -----
    path("calendar/", calendar_view, name="calendar"),
    path("calendar/add-event/", add_event, name="add_event"),
    path("calendar/delete-event/<str:event_id>/", delete_event, name="delete_event"),

    # Google OAuth (Calendar linking)
    path("google/authorize/", authorize, name="authorize"),
    path("google/callback/", oauth2callback, name="oauth2callback"),
]
