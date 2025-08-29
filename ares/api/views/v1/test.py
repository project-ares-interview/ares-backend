from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ares.utils.example_util import example

class TestView(APIView):
    def get(self, request, *args, **kwargs):
        return Response({"status": example()}, status=status.HTTP_200_OK)