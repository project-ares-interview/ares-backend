# ares/api/routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/interview/results/(?P<sid>[\w-]+)/$', consumers.InterviewResultsConsumer.as_asgi()),
    re_path(r'ws/interview/audio/(?P<sid>[\w-]+)/$', consumers.InterviewAudioConsumer.as_asgi()),
    re_path(r'ws/interview/video/(?P<sid>[\w-]+)/$', consumers.InterviewVideoConsumer.as_asgi()),
]
