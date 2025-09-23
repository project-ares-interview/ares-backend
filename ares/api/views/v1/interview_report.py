from __future__ import annotations
from django.utils.dateparse import parse_date
from rest_framework import permissions, mixins, status
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from drf_spectacular.utils import extend_schema

from ares.api.models.interview_report import InterviewReport
from ares.api.serializers.v1.interview_report import (
    InterviewReportListOut,
    InterviewReportDetailOut,
)


class IsOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.user_id == getattr(request.user, 'id', None)


class InterviewReportViewSet(mixins.ListModelMixin,
                             mixins.RetrieveModelMixin,
                             GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = InterviewReport.objects.filter(user=self.request.user).order_by('-created_at')
        # filters
        session_id = self.request.query_params.get('session_id')
        tag = self.request.query_params.get('tag')
        from_date = self.request.query_params.get('from')
        to_date = self.request.query_params.get('to')
        if session_id:
            qs = qs.filter(session_id=session_id)
        if tag:
            qs = qs.filter(tags__contains=[tag])
        if from_date:
            d = parse_date(from_date)
            if d:
                qs = qs.filter(created_at__date__gte=d)
        if to_date:
            d = parse_date(to_date)
            if d:
                qs = qs.filter(created_at__date__lte=d)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return InterviewReportListOut
        return InterviewReportDetailOut

    @extend_schema(summary="List Interview Reports")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(summary="Retrieve Interview Report")
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.user_id != request.user.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(instance)
        data = serializer.to_representation({
            "id": instance.id,
            "session": getattr(instance.session, 'id', None),
            "overall_summary": instance.overall_summary,
            "interview_flow_rationale": instance.interview_flow_rationale,
            "strengths_matrix": instance.strengths_matrix,
            "weaknesses_matrix": instance.weaknesses_matrix,
            "score_aggregation": instance.score_aggregation,
            "missed_opportunities": instance.missed_opportunities,
            "potential_followups_global": instance.potential_followups_global,
            "resume_feedback": instance.resume_feedback,
            "hiring_recommendation": instance.hiring_recommendation,
            "next_actions": instance.next_actions,
            "question_by_question_feedback": instance.question_by_question_feedback,
            "tags": instance.tags,
            "version": instance.version,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
        })
        return Response(data)

    def list_queryset_to_representation(self, queryset):
        items = []
        for r in queryset:
            items.append({
                "id": r.id,
                "session": getattr(r.session, 'id', None),
                "overall_summary": r.overall_summary,
                "hiring_recommendation": r.hiring_recommendation,
                "created_at": r.created_at,
            })
        return items

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        items = self.list_queryset_to_representation(page or queryset)
        serializer = self.get_serializer(items, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
