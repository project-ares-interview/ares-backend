from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ares.api.views.v1.example import ExampleViewSet

router = DefaultRouter()
router.register(r"examples", ExampleViewSet, basename="example")

urlpatterns = [
    path("", include(router.urls)),
]
