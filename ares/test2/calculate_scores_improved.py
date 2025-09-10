# -*- coding: utf-8 -*-

# AI Hub 면접 데이터 점수 계산 스크립트 (개선된 정규화 버전)

# 2단계: 추출된 features를 기반으로 벡터화된 점수 계산 + 정규화

import time
import os
import pandas as pd
import numpy as np
from datetime import datetime

# 전역 상수로 성별 기준값 정의 (반복 호출 최소화)
GENDER_NORMS = {
    'MALE': {
        'intensity': 58.0,
        'f0_mean': 120.0,
        'spectral_centroid': 1400.0,
        'jitter': 0.008,
        'shimmer': 0.025
    },
    'FEMALE': {
        'intensity': 55.0,
        'f0_mean': 200.0,
        'spectral_centroid': 1800.0,
        'jitter': 0.009,
        'shimmer': 0.028
    },
    'unknown': {
        'intensity': 56.5,
        'f0_mean': 160.0,
        'spectral_centroid': 1600.0,
        'jitter': 0.0085,
        'shimmer': 0.0265
    }
}

def vectorized_sigmoid(values, center=0, steepness=1.0, min_val=0.0, max_val=1.0):
    """벡터화된 시그모이드 변환 함수"""
    try:
        # 오버플로우 방지
        exponent = -steepness * (values - center)
        exponent = np.clip(exponent, -500, 500)  # exp 오버플로우 방지
        sigmoid_vals = 1.0 / (1.0 + np.exp(exponent))
        scaled_vals = min_val + (max_val - min_val) * sigmoid_vals
        return scaled_vals.astype(np.float64)
    except:
        return np.full_like(values, (min_val + max_val) / 2, dtype=np.float64)

def vectorized_gaussian(values, optimal, tolerance, min_score=0.0, max_score=1.0):
    """벡터화된 가우시안 점수 계산"""
    try:
        exponent = -0.5 * ((values - optimal) / tolerance) ** 2
        exponent = np.clip(exponent, -500, 0)  # exp 오버플로우 방지
        gaussian_vals = np.exp(exponent)
        scaled_scores = min_score + (max_score - min_score) * gaussian_vals
        return scaled_scores.astype(np.float64)
    except:
        return np.full_like(values, min_score, dtype=np.float64)

def robust_normalize_scores(scores, target_min=0, target_max=100, target_mean=50, target_std=15):
    """
    견고한 점수 정규화 함수 (scipy 의존성 없음)
    
    Parameters:
    - scores: 원본 점수 배열
    - target_min, target_max: 목표 최솟값, 최댓값  
    - target_mean, target_std: 목표 평균, 표준편차
    
    Returns:
    - normalized_scores: 정규화된 점수 배열
    """
    scores = np.array(scores)
    
    if len(scores) == 0:
        return scores
    
    # 1단계: 백분위 기반 극값 제거 (2%-98% 범위)
    p2 = np.percentile(scores, 2)
    p98 = np.percentile(scores, 98)
    clipped_scores = np.clip(scores, p2, p98)
    
    # 2단계: Min-Max 정규화로 0-1 범위 변환
    score_min, score_max = clipped_scores.min(), clipped_scores.max()
    
    if score_max - score_min > 1e-6:  # 거의 동일한 값들 처리
        normalized_01 = (clipped_scores - score_min) / (score_max - score_min)
    else:
        normalized_01 = np.full_like(clipped_scores, 0.5)
    
    # 3단계: 순위 기반 균등 분포 변환 (더 균등한 분포 생성)
    # 각 점수의 순위를 구해서 균등 분포로 변환
    ranks = np.argsort(np.argsort(normalized_01))
    uniform_scores = (ranks + 0.5) / len(ranks)  # 0-1 균등 분포
    
    # 4단계: 정규분포 근사 (Box-Muller 변환의 간단한 버전)
    # 균등분포를 정규분포로 변환 (inverse normal CDF 근사)
    u = uniform_scores
    u = np.clip(u, 0.0001, 0.9999)  # 극값 방지
    
    # 정규분포의 역함수 근사 (Beasley-Springer-Moro 방법의 단순화)
    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308
    
    # u > 0.5인 경우와 u <= 0.5인 경우 처리
    mask = u > 0.5
    t = np.where(mask, np.sqrt(-2.0 * np.log(1.0 - u)), np.sqrt(-2.0 * np.log(u)))
    
    numerator = c0 + c1 * t + c2 * t * t
    denominator = 1.0 + d1 * t + d2 * t * t + d3 * t * t * t
    z_scores = np.where(mask, 1.0, -1.0) * (t - numerator / denominator)
    
    # 5단계: 목표 평균과 표준편차로 스케일링
    final_scores = z_scores * target_std + target_mean
    
    # 6단계: 부드러운 범위 조정 (tanh 사용)
    range_center = (target_min + target_max) / 2
    range_half = (target_max - target_min) / 2
    
    # 극값을 부드럽게 조정
    normalized_input = (final_scores - range_center) / range_half
    # tanh로 -1 ~ +1 범위로 부드럽게 제한
    soft_clipped = np.tanh(normalized_input) * range_half + range_center
    
    return soft_clipped

def calculate_all_scores_vectorized(df):
    """전체 DataFrame에 대한 벡터화된 점수 계산 (정규화 적용)"""
    print("🚀 벡터화된 점수 계산 시작...")
    n_samples = len(df)
    
    # 성별별 기준값 매핑 (벡터화)
    gender_intensity_norms = df['gender'].map(
        lambda x: GENDER_NORMS.get(x, GENDER_NORMS['unknown'])['intensity']
    ).values
    
    gender_spectral_norms = df['gender'].map(
        lambda x: GENDER_NORMS.get(x, GENDER_NORMS['unknown'])['spectral_centroid']
    ).values
    
    # ============ 자신감 점수 (벡터화) ============
    print(" 📊 자신감 점수 계산 및 정규화...")
    
    # 1. 음성 강도 기반 (50%)
    intensity_norm = df['intensity_mean'].values / gender_intensity_norms
    intensity_scores = vectorized_sigmoid(intensity_norm, center=1.0, steepness=2.0) * 100
    
    # 2. 피치 안정성 (30%)
    f0_cv = df['f0_std'].values / np.maximum(df['f0_mean'].values, 1.0)
    f0_stability_scores = vectorized_gaussian(f0_cv, optimal=0.15, tolerance=0.08) * 100
    
    # 3. 음성 품질 (20%) - Jitter/Shimmer
    jitter_scores = np.maximum(0, 100 - df['jitter'].values * 10000)
    shimmer_scores = np.maximum(0, 100 - df['shimmer'].values * 100)
    quality_scores = (jitter_scores + shimmer_scores) / 2
    
    confidence_scores_raw = (intensity_scores * 0.5 + 
                           f0_stability_scores * 0.3 + 
                           quality_scores * 0.2)
    
    # 정규화 적용
    confidence_scores = robust_normalize_scores(confidence_scores_raw)
    
    # ============ 유창성 점수 (벡터화) ============
    print(" 🗣️ 유창성 점수 계산 및 정규화...")
    
    # 1. 말하기 속도 (50%)
    wpm_values = df['wpm'].values
    speed_scores = np.where(
        wpm_values > 0,
        vectorized_gaussian(wpm_values, optimal=160, tolerance=30) * 100,
        70.0
    )
    
    # 2. 음성 연속성 (30%)
    voiced_scores = vectorized_gaussian(df['voiced_ratio'].values, optimal=0.45, tolerance=0.15) * 100
    
    # 3. 스펙트럴 안정성 (20%)
    spectral_stability_scores = np.maximum(0, 100 - df['zcr_mean'].values * 300)
    
    fluency_scores_raw = (speed_scores * 0.5 + 
                         voiced_scores * 0.3 + 
                         spectral_stability_scores * 0.2)
    
    # 정규화 적용
    fluency_scores = robust_normalize_scores(fluency_scores_raw)
    
    # ============ 안정성 점수 (벡터화) ============
    print(" 🎯 안정성 점수 계산 및 정규화...")
    
    # 1. 피치 변동 일관성 (60%)
    pitch_stability_scores = vectorized_gaussian(f0_cv, optimal=0.12, tolerance=0.08) * 100
    
    # 2. 강도 일관성 (40%)
    intensity_cv = df['intensity_std'].values / np.maximum(df['intensity_mean'].values, 1.0)
    intensity_stability_scores = vectorized_gaussian(intensity_cv, optimal=0.2, tolerance=0.1) * 100
    
    stability_scores_raw = (pitch_stability_scores * 0.6 + intensity_stability_scores * 0.4)
    
    # 정규화 적용
    stability_scores = robust_normalize_scores(stability_scores_raw)
    
    # ============ 명료성 점수 (벡터화) ============
    print(" 🔊 명료성 점수 계산 및 정규화...")
    
    # 1. 스펙트럴 명료성 (50%)
    spectral_scores = vectorized_gaussian(
        df['spectral_centroid_mean'].values,
        optimal=gender_spectral_norms,
        tolerance=600
    ) * 100
    
    # 2. 음성 대역폭 (30%)
    bandwidth_scores = vectorized_sigmoid(
        df['spectral_bandwidth_mean'].values,
        center=1200,
        steepness=0.002
    ) * 100
    
    # 3. MFCC 일관성 (20%)
    mfcc_consistency_scores = np.maximum(0, 100 - df['mfcc_std'].values * 15)
    
    clarity_scores_raw = (spectral_scores * 0.5 + 
                         bandwidth_scores * 0.3 + 
                         mfcc_consistency_scores * 0.2)
    
    # 정규화 적용
    clarity_scores = robust_normalize_scores(clarity_scores_raw)
    
    # ============ 종합 점수 (가중평균, 정규화 적용) ============
    print(" 🏆 종합 점수 계산 및 정규화...")
    overall_scores_raw = (confidence_scores * 0.3 + 
                         fluency_scores * 0.3 + 
                         stability_scores * 0.2 + 
                         clarity_scores * 0.2)
    
    # 종합 점수도 정규화 (더 부드러운 설정)
    overall_scores = robust_normalize_scores(overall_scores_raw, target_mean=50, target_std=12)
    
    print("✅ 벡터화된 점수 계산 및 정규화 완료")
    
    # 결과를 DataFrame으로 반환
    scores_df = pd.DataFrame({
        'confidence_score': np.round(confidence_scores, 2),
        'fluency_score': np.round(fluency_scores, 2),
        'stability_score': np.round(stability_scores, 2),
        'clarity_score': np.round(clarity_scores, 2),
        'overall_score': np.round(overall_scores, 2)
    })
    
    return scores_df

class OptimizedScoreCalculator:
    """최적화된 점수 계산기 (정규화 적용)"""
    
    def __init__(self, features_csv_path):
        self.features_csv_path = features_csv_path
        self.df = None
    
    def load_features(self):
        """특성 CSV 로드 및 검증"""
        try:
            self.df = pd.read_csv(self.features_csv_path, encoding='utf-8-sig')
            print(f"✅ 특성 로드 완료: {len(self.df)}개 파일, {len(self.df.columns)}개 특성")
            
            # 필수 컬럼 확인
            required_cols = ['f0_mean', 'f0_std', 'intensity_mean', 'intensity_std',
                           'jitter', 'shimmer', 'voiced_ratio', 'spectral_centroid_mean',
                           'spectral_bandwidth_mean', 'zcr_mean', 'mfcc_std', 'wpm', 'gender']
            
            missing_cols = [col for col in required_cols if col not in self.df.columns]
            if missing_cols:
                print(f"⚠️ 누락된 컬럼: {missing_cols}")
                return False
            
            # 데이터 타입 최적화
            numeric_cols = ['f0_mean', 'f0_std', 'intensity_mean', 'intensity_std',
                          'jitter', 'shimmer', 'voiced_ratio', 'spectral_centroid_mean',
                          'spectral_bandwidth_mean', 'zcr_mean', 'mfcc_std', 'wpm']
            
            for col in numeric_cols:
                if col in self.df.columns:
                    self.df[col] = pd.to_numeric(self.df[col], errors='coerce')
            
            # NaN 값 처리
            self.df = self.df.fillna({
                'f0_mean': 150.0, 'f0_std': 30.0, 'wpm': 0.0,
                'intensity_mean': 55.0, 'intensity_std': 8.0,
                'jitter': 0.01, 'shimmer': 0.03, 'voiced_ratio': 0.5,
                'spectral_centroid_mean': 1500.0, 'spectral_bandwidth_mean': 1200.0,
                'zcr_mean': 0.1, 'mfcc_std': 5.0, 'gender': 'unknown'
            })
            
            return True
            
        except Exception as e:
            print(f"❌ 특성 로드 실패: {e}")
            return False
    
    def calculate_scores(self):
        """벡터화된 점수 계산"""
        if self.df is None:
            if not self.load_features():
                return None
        
        start_time = time.time()
        print(f"🚀 {len(self.df)}개 파일 점수 계산 시작...")
        
        # 벡터화된 점수 계산
        scores_df = calculate_all_scores_vectorized(self.df)
        
        # 원본 데이터와 점수 합치기
        result_df = pd.concat([self.df, scores_df], axis=1)
        
        elapsed = time.time() - start_time
        print(f"✅ 점수 계산 완료: {elapsed:.2f}초 ({len(self.df)/elapsed:.1f}파일/초)")
        
        return result_df
    
    def save_results(self, df, output_path):
        """최적화된 결과 저장"""
        try:
            # 메모리 효율적인 저장
            df.to_csv(output_path, index=False, encoding='utf-8-sig', chunksize=1000)
            print(f"💾 결과 저장 완료: {output_path}")
            
            # 기본 통계 (벡터화된 계산)
            score_columns = ['confidence_score', 'fluency_score', 'stability_score', 'clarity_score', 'overall_score']
            stats = df[score_columns].describe().round(2)
            print(f"\n📊 정규화된 점수 통계:")
            print(stats)
            
            # 목표 달성 확인
            print(f"\n🎯 정규화 목표 달성도:")
            for col in score_columns:
                data = df[col]
                range_usage = (data.max() - data.min()) / 100 * 100
                print(f"  • {col}:")
                print(f"    - 범위: {data.min():.1f} ~ {data.max():.1f} (활용도: {range_usage:.1f}%)")
                print(f"    - 평균: {data.mean():.1f} (목표: 50)")
                print(f"    - 표준편차: {data.std():.1f} (목표: 15)")
            
            # 그룹별 통계 (groupby 최적화)
            if 'occupation' in df.columns:
                print(f"\n💼 직군별 평균 점수:")
                occ_stats = df.groupby('occupation', observed=True)['overall_score'].agg(['count', 'mean', 'std']).round(2)
                print(occ_stats.head(10))  # 상위 10개만 출력
            
            if 'gender' in df.columns:
                print(f"\n👥 성별 평균 점수:")
                gender_stats = df.groupby('gender', observed=True)['overall_score'].agg(['count', 'mean', 'std']).round(2)
                print(gender_stats)
                
        except Exception as e:
            print(f"❌ 저장 실패: {e}")

def main():
    """메인 실행 함수"""
    print("🎯 AI Hub 면접 데이터 점수 계산 시작 (정규화 개선 버전)")
    print("="*60)
    print("📈 정규화 목표:")
    print("  • 각 점수 범위: 0-100점")
    print("  • 평균: 50점, 표준편차: 15점")
    print("  • 정규분포에 가까운 균등한 분포")
    print("="*60)
    
    # 입력/출력 파일 설정
    features_csv = "extracted_features.csv"
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv = f"interview_scores_{now}_normalized.csv"
    
    if not os.path.exists(features_csv):
        print(f"❌ 특성 파일이 없습니다: {features_csv}")
        print("먼저 extract_features_optimized.py를 실행하세요.")
        input("엔터 키를 눌러 종료...")
        return
    
    # 점수 계산
    calculator = OptimizedScoreCalculator(features_csv)
    results_df = calculator.calculate_scores()
    
    if results_df is not None:
        calculator.save_results(results_df, output_csv)
        print(f"\n🎉 정규화된 점수 계산 완료!")
        print(f"📄 결과 파일: {output_csv}")
        print(f"📊 총 {len(results_df)}개 파일 분석")
        
        # 요약 (벡터화된 통계)
        overall_scores = results_df['overall_score'].values
        print(f"\n📈 최종 요약:")
        print(f"• 평균 종합 점수: {np.mean(overall_scores):.1f}점")
        print(f"• 최고 점수: {np.max(overall_scores):.1f}점")
        print(f"• 최저 점수: {np.min(overall_scores):.1f}점")
        print(f"• 표준편차: {np.std(overall_scores):.1f}점")
        print(f"• 0-100 범위 활용도: {((overall_scores.max() - overall_scores.min()) / 100 * 100):.1f}%")
        
        # 분포 품질 확인
        p25, p50, p75 = np.percentile(overall_scores, [25, 50, 75])
        print(f"• 25% 구간: {p25:.1f}점")
        print(f"• 50% 구간(중간값): {p50:.1f}점") 
        print(f"• 75% 구간: {p75:.1f}점")
        
    else:
        print("❌ 점수 계산 실패")
    
    input("엔터 키를 눌러 종료...")

if __name__ == "__main__":
    main()