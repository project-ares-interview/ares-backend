# ares/api/urls.py
from django.urls import include, path

app_name = "api"

urlpatterns = [
    path("v1/", include("ares.api.views.v1.urls")),
]
