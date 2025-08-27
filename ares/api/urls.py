from django.urls import path, include

urlpatterns = [
    path("v1/", include("ares.api.views.v1.urls")),
]
