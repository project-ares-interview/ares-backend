from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ares.api.views.v1.example import ExampleViewSet
from ares.api.views.v1.test import TestView

router = DefaultRouter()
router.register(r"examples", ExampleViewSet, basename="example")

urlpatterns = [
    path("", include(router.urls)),
    path("test", TestView.as_view(), name="test"),
]
