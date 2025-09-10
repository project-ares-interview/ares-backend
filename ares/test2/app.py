from flask import Flask, render_template, request, jsonify
import pandas as pd
import numpy as np
import os
from datetime import datetime
import json

app = Flask(__name__)

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
            # 현재 디렉토리에서 interview_scores로 시작하는 CSV 파일들 찾기
            csv_files = [f for f in os.listdir('.') if f.startswith('interview_scores_') and f.endswith('.csv')]

            if not csv_files:
                print("❌ interview_scores CSV 파일을 찾을 수 없습니다.")
                return False

            # 가장 최신 파일 선택 (파일명의 날짜/시간 기준)
            latest_file = sorted(csv_files)[-1]

            self.df = pd.read_csv(latest_file, encoding='utf-8-sig')
            print(f"✅ CSV 로드 완료: {latest_file} ({len(self.df)}개 데이터)")

            # 점수 컬럼들이 존재하는지 확인
            missing_cols = [col for col in self.score_columns if col not in self.df.columns]
            if missing_cols:
                print(f"⚠️ 누락된 점수 컬럼: {missing_cols}")
                return False

            # 데모그래픽 컬럼 확인
            demo_cols = ['gender', 'ageRange', 'occupation']
            available_demo_cols = [col for col in demo_cols if col in self.df.columns]
            print(f"📊 사용 가능한 데모그래픽 컬럼: {available_demo_cols}")

            return True

        except Exception as e:
            print(f"❌ CSV 로드 실패: {e}")
            return False

    def calculate_percentile(self, score, score_type, filters=None):
        """특정 점수의 백분위 계산 (필터링 옵션 포함)"""
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
        """점수 분포 데이터 반환 (히스토그램용, 필터링 옵션 포함)"""
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

    def get_all_statistics(self):
        """전체 점수 통계 반환"""
        if self.df is None:
            return None

        stats = {}
        for col in self.score_columns:
            if col in self.df.columns:
                score_data = self.df[col].dropna()
                stats[col] = {
                    'name': self.score_names[col],
                    'count': len(score_data),
                    'mean': round(float(score_data.mean()), 1),
                    'std': round(float(score_data.std()), 1),
                    'min': round(float(score_data.min()), 1),
                    'max': round(float(score_data.max()), 1),
                    'percentiles': {
                        '25': round(float(score_data.quantile(0.25)), 1),
                        '50': round(float(score_data.quantile(0.50)), 1),
                        '75': round(float(score_data.quantile(0.75)), 1),
                        '90': round(float(score_data.quantile(0.90)), 1),
                        '95': round(float(score_data.quantile(0.95)), 1)
                    }
                }

        return stats

    def get_demographic_options(self):
        """데모그래픽 필터 옵션 반환"""
        if self.df is None:
            return {}

        demo_options = {}

        # 성별 옵션
        if 'gender' in self.df.columns:
            demo_options['gender'] = sorted(self.df['gender'].dropna().unique().tolist())

        # 연령대 옵션
        if 'ageRange' in self.df.columns:
            demo_options['ageRange'] = sorted(self.df['ageRange'].dropna().unique().tolist())

        # 직군 옵션
        if 'occupation' in self.df.columns:
            demo_options['occupation'] = sorted(self.df['occupation'].dropna().unique().tolist())

        return demo_options

# 전역 분석기 인스턴스
analyzer = ScoreAnalyzer()

@app.route('/')
def index():
    """메인 페이지"""
    if analyzer.df is None:
        return render_template('error.html', 
                             message="CSV 데이터를 로드할 수 없습니다. interview_scores_*.csv 파일이 있는지 확인하세요.")

    stats = analyzer.get_all_statistics()
    demo_options = analyzer.get_demographic_options()

    return render_template('index.html', 
                         stats=stats, 
                         score_names=analyzer.score_names,
                         demo_options=demo_options)

@app.route('/api/analyze', methods=['POST'])
def analyze_scores():
    """점수 분석 API"""
    try:
        data = request.get_json()

        # 점수 데이터 추출
        scores = {}
        for score_type in analyzer.score_columns:
            if score_type in data and data[score_type] is not None:
                score = float(data[score_type])

                # 점수 유효성 검사
                if not (0 <= score <= 100):
                    return jsonify({'error': f'{analyzer.score_names[score_type]} 점수는 0-100 사이여야 합니다.'}), 400

                scores[score_type] = score

        # 데모그래픽 필터 추출
        demographic_filters = {}
        user_demographics = {}

        demo_fields = ['gender', 'ageRange', 'occupation']
        for field in demo_fields:
            if field in data and data[field]:
                demographic_filters[field] = data[field]
                user_demographics[field] = data[field]

        results = {}
        for score_type, score in scores.items():
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
            if demographic_filters:
                filtered_percentile = analyzer.calculate_percentile(score, score_type, demographic_filters)
                filtered_distribution = analyzer.get_score_distribution(score_type, demographic_filters)

                result_data['filtered_percentile'] = filtered_percentile
                result_data['filtered_distribution'] = filtered_distribution
                result_data['filters'] = demographic_filters

            results[score_type] = result_data

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': f'분석 중 오류 발생: {str(e)}'}), 500

@app.route('/api/distribution/<score_type>')
def get_distribution(score_type):
    """특정 점수 타입의 분포 데이터 반환"""
    if score_type not in analyzer.score_columns:
        return jsonify({'error': '유효하지 않은 점수 타입'}), 400

    # 필터 파라미터 추출
    filters = {}
    demo_fields = ['gender', 'ageRange', 'occupation']
    for field in demo_fields:
        if request.args.get(field):
            filters[field] = request.args.get(field)

    distribution = analyzer.get_score_distribution(score_type, filters if filters else None)
    if distribution is None:
        return jsonify({'error': '분포 데이터를 가져올 수 없습니다.'}), 500

    return jsonify(distribution)

@app.route('/api/stats')
def get_stats():
    """전체 통계 데이터 반환"""
    stats = analyzer.get_all_statistics()
    if stats is None:
        return jsonify({'error': '통계 데이터를 가져올 수 없습니다.'}), 500

    return jsonify(stats)

@app.route('/api/demographics')
def get_demographics():
    """데모그래픽 옵션 반환"""
    demo_options = analyzer.get_demographic_options()
    return jsonify(demo_options)

if __name__ == '__main__':
    print("🚀 면접 점수 분석 웹 애플리케이션 시작")
    print("="*50)

    if analyzer.df is not None:
        print(f"📊 데이터: {len(analyzer.df)}개 면접 기록")
        print(f"📈 분석 가능 점수: {', '.join([analyzer.score_names[col] for col in analyzer.score_columns])}")

        # 데모그래픽 정보 출력
        demo_options = analyzer.get_demographic_options()
        if demo_options:
            print("👥 데모그래픽 필터:")
            for demo_type, options in demo_options.items():
                print(f"   • {demo_type}: {len(options)}개 옵션")

        print("\n🌐 웹 서버 시작: http://localhost:5000")
    else:
        print("❌ CSV 데이터 로드 실패 - 웹 서버는 시작되지만 오류 페이지가 표시됩니다.")

    app.run(debug=True, host='0.0.0.0', port=5000)
