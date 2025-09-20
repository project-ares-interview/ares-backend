# ares/api/views/v1/interview/find_companies.py
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ares.api.serializers.v1.interview import (
    FindCompaniesRequestSerializer,
    FindCompaniesResponseSerializer,
)
from ares.api.services.company_data import find_affiliates_by_keyword


class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Find Affiliate Companies",
        request=FindCompaniesRequestSerializer,
        responses=FindCompaniesResponseSerializer,
    )
    def post(self, request, *args, **kwargs):
        keyword = (request.data or {}).get("keyword", "")
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)
