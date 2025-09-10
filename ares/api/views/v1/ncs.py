# ares/api/views/v1/ncs.py
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ares.api.utils.search_utils import search_ncs_hybrid
from ares.api.services.ncs_service import summarize_top_ncs

class NCSViewSet(viewsets.ViewSet):
    """
    /v1/ncs/search/  (GET)  ?q=...&top=8
    /v1/ncs/top/     (POST) {job_title, jd_text, top}
    """

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        q = (request.query_params.get("q") or "").strip()
        top = int(request.query_params.get("top", 8))
        if not q:
            return Response({"detail": "query param 'q' is required"}, status=status.HTTP_400_BAD_REQUEST)
        hits = search_ncs_hybrid(q, top=top)
        return Response(hits, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="top")
    def top(self, request):
        job_title = (request.data.get("job_title") or "").strip()
        jd_text   = (request.data.get("jd_text") or "").strip()
        top       = int(request.data.get("top", 8))
        if not (job_title or jd_text):
            return Response({"detail": "body must include 'job_title' or 'jd_text'"},
                            status=status.HTTP_400_BAD_REQUEST)
        summary = summarize_top_ncs(job_title, jd_text, top=top)
        return Response(summary, status=status.HTTP_200_OK)
