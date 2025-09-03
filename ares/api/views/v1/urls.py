from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ares.api.views.v1.example import ExampleViewSet
from ares.api.views.v1.ncs import NCSViewSet  # ✅ 추가

router = DefaultRouter()
router.register(r"examples", ExampleViewSet, basename="example")
router.register(r"ncs", NCSViewSet, basename="ncs")  # ✅ 추가

urlpatterns = [
    path("", include(router.urls)),
]
