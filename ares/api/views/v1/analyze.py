# ares/api/views/v1/analyze.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
import pandas as pd
import numpy as np
import os
import random
from pathlib import Path
from django.http import JsonResponse

# New import for the percentile service
from ares.api.services.percentile_service import percentile_service
# New import for the AI advisor service
from ares.api.services.openai_advisor import advisor as ai_advisor


class GenerateAIAdviceView(APIView):
    """
    API view to generate AI-based interview advice.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        """
        Generates advice based on the provided analysis data.
        """
        analysis_data = request.data
        if not analysis_data:
            return Response(
                {'error': 'Analysis data was not provided.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Use the imported advisor instance to generate advice
            advice_result = ai_advisor.generate_advice(analysis_data)
            return Response(advice_result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': f'An unexpected error occurred while generating advice: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PercentileAnalysisView(APIView):
    """
    API view to calculate and return score percentiles based on filters.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """
        Calculates percentiles for given scores based on query parameters.
        
        Query Params:
        - Scores (required): e.g., confidence_score=85, fluency_score=92
        - Filters (optional): e.g., gender=MALE, ageRange=-34, occupation=ICT
          (Filters can have multiple values, e.g., &occupation=ICT&occupation=RND)
        """
        try:
            # 1. Parse scores from query parameters
            score_columns = ['confidence_score', 'fluency_score', 'stability_score', 'clarity_score', 'overall_score']
            user_scores = {}
            for score in score_columns:
                value = request.query_params.get(score)
                if value is not None:
                    try:
                        user_scores[score] = float(value)
                    except (ValueError, TypeError):
                        return Response(
                            {'error': f'Invalid value for score: {score}. Must be a number.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
            
            if not user_scores:
                return Response(
                    {'error': 'At least one score parameter is required.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 2. Parse filters from query parameters
            filter_keys = ['gender', 'ageRange', 'occupation']
            filters = {}
            for key in filter_keys:
                values = request.query_params.getlist(key)
                if values:
                    filters[key] = values

            # 3. Calculate percentiles using the service
            percentiles = percentile_service.get_percentiles(user_scores, filters)

            return Response(percentiles, status=status.HTTP_200_OK)

        except Exception as e:
            # In a real scenario, you might want to log this error.
            return Response(
                {'error': f'An unexpected error occurred during percentile analysis: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ScoreAnalyzer:
    def __init__(self):
        self.df = None
        self.score_columns = ['confidence_score', 'fluency_score', 'stability_score', 'clarity_score', 'overall_score']
        self.score_names = {
            'confidence_score': '자신감',
            'fluency_score': '유창성',
            'stability_score': '안정성',
            'clarity_score': '명료성',
            'overall_score': '종합'
        }
        self.load_latest_csv()

    def load_latest_csv(self):
        """가장 최신의 interview_scores CSV 파일 로드"""
        try:
            # ares/data/ 디렉토리에서 interview_scores로 시작하는 CSV 파일들 찾기
            data_dir = Path('ares/data')
            csv_paths = [str(p) for p in data_dir.glob('interview_scores_*.csv')]

            if not csv_paths:
                print("❌ interview_scores CSV 파일을 찾을 수 없습니다.")
                return False

            # 가장 최신 파일 선택 (파일명의 날짜/시간 기준)
            latest_file = sorted(csv_paths)[-1]
            self.df = pd.read_csv(latest_file, encoding='utf-8-sig')
            print(f"✅ CSV 로드 완료: {latest_file} ({len(self.df)}개 데이터)")

            # 점수 컬럼들이 존재하는지 확인
            missing_cols = [col for col in self.score_columns if col not in self.df.columns]
            if missing_cols:
                print(f"⚠️ 누락된 점수 컬럼: {missing_cols}")
                return False

            return True

        except Exception as e:
            print(f"❌ CSV 로드 실패: {e}")
            return False

    def calculate_percentile(self, score, score_type, filters=None):
        """특정 점수의 백분위 계산"""
        if self.df is None or score_type not in self.score_columns:
            return None

        # 필터링 적용
        filtered_df = self.df.copy()
        if filters:
            for filter_type, filter_value in filters.items():
                if filter_type in filtered_df.columns:
                    filtered_df = filtered_df[filtered_df[filter_type] == filter_value]

        score_data = filtered_df[score_type].dropna()
        if len(score_data) == 0:
            return None

        percentile = (score_data < score).sum() / len(score_data) * 100
        return round(percentile, 1)

    def get_score_distribution(self, score_type, filters=None):
        """점수 분포 데이터 반환"""
        if self.df is None or score_type not in self.score_columns:
            return None

        # 필터링 적용
        filtered_df = self.df.copy()
        if filters:
            for filter_type, filter_value in filters.items():
                if filter_type in filtered_df.columns:
                    filtered_df = filtered_df[filtered_df[filter_type] == filter_value]

        score_data = filtered_df[score_type].dropna()
        if len(score_data) == 0:
            return None

        # 히스토그램 데이터 생성 (20개 구간)
        hist, bin_edges = np.histogram(score_data, bins=20, range=(0, 100))

        # 구간 중앙값 계산
        bin_centers = [(bin_edges[i] + bin_edges[i+1]) / 2 for i in range(len(bin_edges)-1)]

        return {
            'bins': bin_centers,
            'counts': hist.tolist(),
            'total_count': len(score_data),
            'mean': float(score_data.mean()),
            'std': float(score_data.std()),
            'min': float(score_data.min()),
            'max': float(score_data.max())
        }

# 전역 분석기 인스턴스
analyzer = ScoreAnalyzer()

class AnalyzeView(APIView):
    """면접 점수 분석 API 뷰"""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """점수 분석 수행"""
        try:
            # 🎲 더미 데이터 생성 (하드코딩)
            dummy_scores = {
                'confidence_score': random.randint(0, 100),
                'fluency_score': random.randint(0, 100),
                'stability_score': random.randint(0, 100),
                'clarity_score': random.randint(0, 100),
                'overall_score': random.randint(0, 100)
            }

            # 🎲 더미 데모그래픽 데이터
            dummy_demographics = {
                'gender': random.choice(['MALE', 'FEMALE']),
                'ageRange': random.choice(['20대', '30대', '40대', '50대']),
                'occupation': random.choice(['개발자', '디자이너', '마케터', '기획자', '영업'])
            }

            print(f"🎲 생성된 더미 점수: {dummy_scores}")
            print(f"🎲 생성된 더미 데모그래픽: {dummy_demographics}")

            # 분석기가 로드되지 않은 경우 에러 반환
            if analyzer.df is None:
                return Response({
                    'error': 'CSV 데이터를 로드할 수 없습니다. interview_scores_*.csv 파일이 있는지 확인하세요.'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            results = {}

            for score_type, score in dummy_scores.items():
                # 전체 데이터 기준 백분위
                overall_percentile = analyzer.calculate_percentile(score, score_type)
                overall_distribution = analyzer.get_score_distribution(score_type)

                result_data = {
                    'score': score,
                    'overall_percentile': overall_percentile,
                    'overall_distribution': overall_distribution,
                    'name': analyzer.score_names[score_type],
                    'demographic_results': {}
                }

                # 데모그래픽 필터별 백분위 계산
                filtered_percentile = analyzer.calculate_percentile(score, score_type, dummy_demographics)
                filtered_distribution = analyzer.get_score_distribution(score_type, dummy_demographics)

                if filtered_percentile is not None:
                    result_data['filtered_percentile'] = filtered_percentile
                if filtered_distribution is not None:
                    result_data['filtered_distribution'] = filtered_distribution
                result_data['filters'] = dummy_demographics

                results[score_type] = result_data

            return Response(results, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                'error': f'분석 중 오류 발생: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)