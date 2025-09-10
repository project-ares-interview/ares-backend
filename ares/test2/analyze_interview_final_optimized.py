# -*- coding: utf-8 -*-
# AI Hub 면접 데이터 최종 분석 스크립트 (최적화 버전)
# 3단계: 점수 CSV를 기반으로 최적화된 분석 및 엑셀 생성

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import sys
import warnings
warnings.filterwarnings('ignore')  # pandas 경고 억제

def find_latest_scores_file():
    """가장 최근 점수 파일 찾기"""
    scores_files = list(Path(".").glob("interview_scores_*.csv"))
    if not scores_files:
        return None
    
    # 파일 수정 시간 기준으로 최신 파일 찾기
    latest_file = max(scores_files, key=os.path.getctime)
    return str(latest_file)

def create_optimized_analysis_excel(df, output_excel):
    """최적화된 분석 결과 엑셀 파일 생성"""
    print(f"💾 엑셀 파일 생성 중: {output_excel}")
    
    score_columns = ['confidence_score', 'fluency_score', 'stability_score', 'clarity_score', 'overall_score']
    
    # 메모리 효율적인 엑셀 작성
    with pd.ExcelWriter(output_excel, engine='openpyxl', options={'remove_timezone': True}) as writer:
        
        # 시트 1: 전체 결과 (샘플링된 데이터로 메모리 절약)
        print("  📊 전체 결과 시트 생성...")
        if len(df) > 10000:  # 10000개 이상이면 샘플링
            sample_df = df.sample(n=10000, random_state=42).copy()
            sample_df.to_excel(writer, sheet_name='전체_분석결과_샘플', index=False)
            print(f"    ⚠️ 데이터가 많아 10000개 샘플링하여 저장")
        else:
            df.to_excel(writer, sheet_name='전체_분석결과', index=False)
        
        # 시트 2: 요약 통계 (벡터화된 계산)
        print("  📈 요약 통계 시트 생성...")
        if all(col in df.columns for col in score_columns):
            summary_stats = df[score_columns].describe().round(2)
            summary_stats.to_excel(writer, sheet_name='요약_통계')
        
        # 시트 3-5: 그룹별 분석 (효율적인 groupby)
        group_analyses = [
            ('occupation', '직군별_분석'),
            ('gender', '성별_분석'),
            ('ageRange', '연령대별_분석')
        ]
        
        for group_col, sheet_name in group_analyses:
            if group_col in df.columns:
                print(f"  👥 {sheet_name} 시트 생성...")
                try:
                    # 메모리 효율적인 그룹바이 연산
                    grouped = df.groupby(group_col, observed=True)[score_columns]
                    analysis = grouped.agg(['count', 'mean', 'std']).round(2)
                    
                    # 빈 그룹 제거
                    analysis = analysis[analysis.iloc[:, 0] > 0]  # count > 0인 그룹만
                    
                    analysis.to_excel(writer, sheet_name=sheet_name)
                except Exception as e:
                    print(f"    ⚠️ {sheet_name} 생성 실패: {e}")
        
        # 시트 6-7: 상위/하위 퍼센타일
        print("  🏆 상위/하위 퍼센타일 시트 생성...")
        try:
            # 벡터화된 정렬 및 선택
            sorted_indices = np.argsort(df['overall_score'].values)
            n_total = len(sorted_indices)
            
            # 상위 10%
            top_10_indices = sorted_indices[-int(n_total * 0.1):]
            top_10_df = df.iloc[top_10_indices].copy()
            top_10_df.to_excel(writer, sheet_name='상위_10퍼센트', index=False)
            
            # 하위 10%
            bottom_10_indices = sorted_indices[:int(n_total * 0.1)]
            bottom_10_df = df.iloc[bottom_10_indices].copy()
            bottom_10_df.to_excel(writer, sheet_name='하위_10퍼센트', index=False)
            
        except Exception as e:
            print(f"    ⚠️ 상위/하위 퍼센타일 시트 생성 실패: {e}")
        
        # 시트 8: 음성 특성 통계
        print("  🎙️ 음성 특성 통계 시트 생성...")
        prosodic_features = ['f0_mean', 'f0_std', 'jitter', 'shimmer', 'voiced_ratio', 
                           'intensity_mean', 'spectral_centroid_mean', 'duration', 'wpm']
        
        available_features = [col for col in prosodic_features if col in df.columns]
        if available_features:
            try:
                feature_stats = df[available_features].describe().round(4)
                feature_stats.to_excel(writer, sheet_name='음성특성_통계')
                
                # 상관관계 분석 (샘플링하여 메모리 절약)
                if len(df) > 5000:
                    corr_sample = df[available_features].sample(n=5000, random_state=42)
                else:
                    corr_sample = df[available_features]
                
                correlation = corr_sample.corr().round(3)
                correlation.to_excel(writer, sheet_name='특성_상관관계')
                
            except Exception as e:
                print(f"    ⚠️ 음성 특성 통계 생성 실패: {e}")
    
    print(f"✅ 엑셀 파일 생성 완료: {output_excel}")

def print_optimized_analysis_summary(df):
    """최적화된 분석 결과 요약 출력"""
    print(f"\n📊 분석 결과 요약")
    print("="*50)
    
    # 기본 통계 (벡터화된 계산)
    overall_scores = df['overall_score'].values
    n_files = len(df)
    
    print(f"총 분석 파일 수: {n_files:,}개")
    print(f"평균 종합 점수: {np.mean(overall_scores):.1f}점")
    print(f"최고 점수: {np.max(overall_scores):.1f}점")
    print(f"최저 점수: {np.min(overall_scores):.1f}점")
    print(f"표준편차: {np.std(overall_scores):.1f}")
    print(f"중위값: {np.median(overall_scores):.1f}점")
    
    # 점수 분포 (벡터화된 계산)
    print(f"\n📈 점수 분포")
    print("-"*30)
    bins = [(90, 100, "우수"), (80, 89, "양호"), (70, 79, "보통"), (60, 69, "개선필요"), (0, 59, "집중연습")]
    
    for min_val, max_val, label in bins:
        mask = (overall_scores >= min_val) & (overall_scores <= max_val)
        count = np.sum(mask)
        percentage = (count / n_files) * 100
        print(f"{label} ({min_val}-{max_val}점): {count:,}개 ({percentage:.1f}%)")
    
    # 그룹별 요약 (효율적인 groupby)
    group_summaries = [
        ('occupation', '💼 직군별 평균 점수'),
        ('gender', '👥 성별 평균 점수'),
        ('ageRange', '📅 연령대별 평균 점수')
    ]
    
    for group_col, title in group_summaries:
        if group_col in df.columns and df[group_col].nunique() > 1:
            print(f"\n{title}")
            print("-"*30)
            
            try:
                # 메모리 효율적인 그룹 통계
                grouped = df.groupby(group_col, observed=True)['overall_score']
                stats = grouped.agg(['mean', 'count']).round(1)
                stats = stats.sort_values('mean', ascending=False)
                
                # 상위 10개만 표시 (메모리 절약)
                for group_name, row in stats.head(10).iterrows():
                    print(f"{group_name}: {row['mean']:.1f}점 ({int(row['count']):,}개 파일)")
                
                if len(stats) > 10:
                    print(f"... 외 {len(stats) - 10}개 그룹")
                    
            except Exception as e:
                print(f"  ⚠️ {group_col} 통계 계산 실패: {e}")
    
    # 성능 지표 (있는 경우만)
    performance_metrics = [
        ('wpm', '⚡ 말하기 속도 (WPM)', ''),
        ('duration', '🎵 음성 길이', '초'),
        ('jitter', '🎙️ Jitter', ''),
        ('shimmer', '🎙️ Shimmer', ''),
        ('voiced_ratio', '🗣️ Voiced Ratio', '')
    ]
    
    for col, title, unit in performance_metrics:
        if col in df.columns:
            values = df[col].values
            valid_values = values[~np.isnan(values)]  # NaN 제거
            
            if len(valid_values) > 0:
                print(f"\n{title}")
                print("-"*30)
                print(f"평균: {np.mean(valid_values):.3f}{unit}")
                print(f"최고: {np.max(valid_values):.3f}{unit}")
                print(f"최저: {np.min(valid_values):.3f}{unit}")
                
                if col == 'js_success' and col in df.columns:
                    success_rate = np.mean(df[col]) * 100
                    print(f"Jitter/Shimmer 계산 성공률: {success_rate:.1f}%")

def load_and_validate_data(scores_file):
    """최적화된 데이터 로딩 및 검증"""
    try:
        print(f"📁 점수 파일 로딩: {scores_file}")
        
        # 청크 단위로 로딩 (메모리 효율)
        chunk_size = 10000
        chunks = []
        
        for chunk in pd.read_csv(scores_file, encoding='utf-8-sig', chunksize=chunk_size):
            chunks.append(chunk)
        
        df = pd.concat(chunks, ignore_index=True)
        print(f"✅ 데이터 로드 완료: {len(df):,}개 파일")
        
        # 메모리 최적화
        # 카테고리 데이터 변환
        categorical_cols = ['occupation', 'gender', 'ageRange', 'experience']
        for col in categorical_cols:
            if col in df.columns:
                df[col] = df[col].astype('category')
        
        # 수치형 데이터 다운캐스팅
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            if df[col].dtype == 'float64':
                df[col] = pd.to_numeric(df[col], downcast='float')
            elif df[col].dtype == 'int64':
                df[col] = pd.to_numeric(df[col], downcast='integer')
        
        print(f"💾 메모리 최적화 완료")
        
        return df
        
    except Exception as e:
        print(f"❌ 데이터 로딩 실패: {e}")
        return None

def main():
    """최적화된 메인 실행 함수"""
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("🎯 AI Hub 면접 데이터 최종 분석 시작 (최적화 버전)")
    print("="*60)
    
    # 1. 최신 점수 파일 찾기
    scores_file = find_latest_scores_file()
    if not scores_file:
        print("❌ 점수 파일을 찾을 수 없습니다.")
        print("먼저 다음 순서로 실행하세요:")
        print("1. extract_features_optimized.py")
        print("2. calculate_scores_optimized.py") 
        input("엔터 키를 눌러 종료...")
        return
    
    print(f"📁 점수 파일: {scores_file}")
    
    # 2. 최적화된 데이터 로드
    df = load_and_validate_data(scores_file)
    if df is None:
        input("엔터 키를 눌러 종료...")
        return
    
    # 3. 기본 검증
    required_score_cols = ['confidence_score', 'fluency_score', 'stability_score', 'clarity_score', 'overall_score']
    missing_cols = [col for col in required_score_cols if col not in df.columns]
    if missing_cols:
        print(f"❌ 필수 점수 컬럼 누락: {missing_cols}")
        input("엔터 키를 눌러 종료...")
        return
    
    # 4. 최적화된 엑셀 파일 생성
    output_excel = f"AI_Hub_면접분석결과_{now}.xlsx"
    
    try:
        start_time = time.time()
        create_optimized_analysis_excel(df, output_excel)
        excel_time = time.time() - start_time
        
        # 5. 최적화된 분석 결과 요약 출력
        print_optimized_analysis_summary(df)
        
        print(f"\n🎉 분석 완료! (엑셀 생성: {excel_time:.1f}초)")
        print(f"📁 결과 파일: {output_excel}")
        print(f"📋 엑셀 파일에 최대 9개 시트가 생성되었습니다:")
        print(" 1. 전체_분석결과 - 모든 파일의 상세 점수 및 특성")
        print(" 2. 요약_통계 - 점수 기본 통계량")
        print(" 3. 직군별_분석 - 직업군별 비교")
        print(" 4. 성별_분석 - 성별 비교") 
        print(" 5. 연령대별_분석 - 연령대별 비교")
        print(" 6. 상위_10퍼센트 - 우수 사례")
        print(" 7. 하위_10퍼센트 - 개선 필요 사례")
        print(" 8. 음성특성_통계 - prosodic features 통계")
        print(" 9. 특성_상관관계 - 특성 간 상관관계")
        
        # 6. 엑셀 파일 열기 제안
        print(f"\n💡 엑셀 파일을 자동으로 열까요?")
        open_excel = input("엑셀 열기 (y/n): ").strip().lower()
        if open_excel in ['y', 'yes', '예']:
            try:
                os.startfile(output_excel)
                print("✅ 엑셀 파일을 열었습니다.")
            except Exception as e:
                print(f"❌ 자동으로 엑셀을 열 수 없습니다: {e}")
                print(f" 수동으로 {output_excel} 파일을 열어주세요.")
                
    except Exception as e:
        print(f"❌ 분석 중 오류 발생: {e}")
        print("\n상세 오류 정보:")
        import traceback
        traceback.print_exc()
    
    finally:
        print(f"\n프로그램을 종료합니다.")
        input("엔터 키를 눌러 주세요...")

if __name__ == "__main__":
    import time  # time 모듈 import 추가
    main()