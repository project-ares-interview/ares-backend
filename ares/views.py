from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response


class HealthCheckView(APIView):
    """
    API server health check
    """

    def get(self, request, *args, **kwargs):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)
