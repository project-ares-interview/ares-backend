from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from ares.api.services.company_data import (
    find_affiliates_by_keyword,
    get_company_description,
)
from ares.api.services.interview_bot import InterviewBot


class FindCompaniesView(APIView):
    """키워드로 계열사 목록을 검색하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        keyword = request.data.get('keyword', '')
        if not keyword:
            return Response({"error": "Keyword is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        company_list = find_affiliates_by_keyword(keyword)
        return Response(company_list, status=status.HTTP_200_OK)


class StartInterviewView(APIView):
    """면접을 시작하고 첫 질문을 반환하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company_name = request.data.get('company_name')
        job_title = request.data.get('job_title')

        if not all([company_name, job_title]):
            return Response({"error": "company_name and job_title are required"}, status=status.HTTP_400_BAD_REQUEST)

        company_description = get_company_description(company_name)
        
        bot = InterviewBot(job_title, company_name, company_description)
        first_question = bot.ask_first_question()
        
        # 대화 상태를 세션에 저장
        request.session['interview_bot'] = bot.conversation_history
        request.session['interview_info'] = {
            'job_title': job_title,
            'company_name': company_name,
            'company_description': company_description,
        }

        return Response({"question": first_question}, status=status.HTTP_200_OK)


class AnalyzeAnswerView(APIView):
    """사용자의 답변을 분석하고 결과를 반환하는 API"""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        answer = request.data.get('answer', '')
        if not answer:
            return Response({"error": "Answer is required"}, status=status.HTTP_400_BAD_REQUEST)

        # 세션에서 면접 정보 복원
        conversation_history = request.session.get('interview_bot')
        interview_info = request.session.get('interview_info')

        if not conversation_history or not interview_info:
            return Response({"error": "Interview session not found. Please start the interview first."}, status=status.HTTP_400_BAD_REQUEST)

        bot = InterviewBot(
            job_title=interview_info['job_title'],
            company_name=interview_info['company_name'],
            company_description=interview_info['company_description']
        )
        bot.conversation_history = conversation_history
        
        current_question = bot.conversation_history[-1]['question']
        analysis_result = bot.analyze_answer(current_question, answer)
        
        # 분석 후 대화 기록 업데이트
        request.session['interview_bot'] = bot.conversation_history

        return Response(analysis_result, status=status.HTTP_200_OK)
