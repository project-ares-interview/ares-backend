# ares/api/views/v1/interview/coach.py
from django.shortcuts import render

def interview_coach_view(request):
    """Renders the AI Interview Coach page."""
    return render(request, "api/interview_coach.html")
