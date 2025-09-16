# ares/api/services/voice_analysis_service.py
import pandas as pd
import numpy as np
import traceback
import librosa # librosa 임포트 추가

# 모듈화된 음향 특성 추출 함수를 임포트합니다.
from ares.api.utils.audio_utils import extract_prosodic_features_from_buffer

# ==============================================================================
# 점수 계산 로직 (calculate_scores_improved.py에서 가져옴)
# ==============================================================================

GENDER_NORMS = {
    'MALE': {
        'intensity': 58.0, 'f0_mean': 120.0, 'spectral_centroid': 1400.0,
        'jitter': 0.008, 'shimmer': 0.025
    },
    'FEMALE': {
        'intensity': 55.0, 'f0_mean': 200.0, 'spectral_centroid': 1800.0,
        'jitter': 0.009, 'shimmer': 0.028
    },
    'unknown': {
        'intensity': 56.5, 'f0_mean': 160.0, 'spectral_centroid': 1600.0,
        'jitter': 0.0085, 'shimmer': 0.0265
    }
}

def vectorized_sigmoid(values, center=0, steepness=1.0, min_val=0.0, max_val=1.0):
    try:
        exponent = -steepness * (values - center)
        exponent = np.clip(exponent, -500, 500)
        sigmoid_vals = 1.0 / (1.0 + np.exp(exponent))
        return min_val + (max_val - min_val) * sigmoid_vals
    except:
        return np.full_like(values, (min_val + max_val) / 2, dtype=np.float64)

def vectorized_gaussian(values, optimal, tolerance, min_score=0.0, max_score=1.0):
    try:
        exponent = -0.5 * ((values - optimal) / tolerance) ** 2
        exponent = np.clip(exponent, -500, 0)
        gaussian_vals = np.exp(exponent)
        return min_score + (max_score - min_score) * gaussian_vals
    except:
        return np.full_like(values, min_score, dtype=np.float64)

def calculate_scores_for_single_file(df):
    """단일 파일(DataFrame row 1개)에 대한 점수 계산. 정규화는 제외."""
    gender_intensity_norms = df['gender'].map(lambda x: GENDER_NORMS.get(x, GENDER_NORMS['unknown'])['intensity']).values
    gender_spectral_norms = df['gender'].map(lambda x: GENDER_NORMS.get(x, GENDER_NORMS['unknown'])['spectral_centroid']).values

    # 자신감 점수
    intensity_norm = df['intensity_mean'].values / gender_intensity_norms
    intensity_scores = vectorized_sigmoid(intensity_norm, center=1.0, steepness=2.0) * 100
    f0_cv = df['f0_std'].values / np.maximum(df['f0_mean'].values, 1.0)
    f0_stability_scores = vectorized_gaussian(f0_cv, optimal=0.15, tolerance=0.08) * 100
    jitter_scores = np.maximum(0, 100 - df['jitter'].values * 10000)
    shimmer_scores = np.maximum(0, 100 - df['shimmer'].values * 100)
    quality_scores = (jitter_scores + shimmer_scores) / 2
    confidence_scores = (intensity_scores * 0.5 + f0_stability_scores * 0.3 + quality_scores * 0.2)

    # 유창성 점수
    wpm_values = df['wpm'].values
    speed_scores = np.where(wpm_values > 0, vectorized_gaussian(wpm_values, optimal=160, tolerance=30) * 100, 70.0)
    voiced_scores = vectorized_gaussian(df['voiced_ratio'].values, optimal=0.45, tolerance=0.15) * 100
    spectral_stability_scores = np.maximum(0, 100 - df['zcr_mean'].values * 300)
    fluency_scores = (speed_scores * 0.5 + voiced_scores * 0.3 + spectral_stability_scores * 0.2)

    # 안정성 점수
    pitch_stability_scores = vectorized_gaussian(f0_cv, optimal=0.12, tolerance=0.08) * 100
    intensity_cv = df['intensity_std'].values / np.maximum(df['intensity_mean'].values, 1.0)
    intensity_stability_scores = vectorized_gaussian(intensity_cv, optimal=0.2, tolerance=0.1) * 100
    stability_scores = (pitch_stability_scores * 0.6 + intensity_stability_scores * 0.4)

    # 명료성 점수
    spectral_scores = vectorized_gaussian(df['spectral_centroid_mean'].values, optimal=gender_spectral_norms, tolerance=600) * 100
    bandwidth_scores = vectorized_sigmoid(df['spectral_bandwidth_mean'].values, center=1200, steepness=0.002) * 100
    mfcc_consistency_scores = np.maximum(0, 100 - df['mfcc_std'].values * 15)
    clarity_scores = (spectral_scores * 0.5 + bandwidth_scores * 0.3 + mfcc_consistency_scores * 0.2)

    # 종합 점수
    overall_scores = (confidence_scores * 0.3 + fluency_scores * 0.3 + stability_scores * 0.2 + clarity_scores * 0.2)

    return pd.DataFrame({
        'confidence_score': np.round(confidence_scores, 2),
        'fluency_score': np.round(fluency_scores, 2),
        'stability_score': np.round(stability_scores, 2),
        'clarity_score': np.round(clarity_scores, 2),
        'overall_score': np.round(overall_scores, 2)
    })

# ==============================================================================
# 서비스 메인 함수
# ==============================================================================

def analyze_voice_from_buffer(audio_buffer: np.ndarray, sr: int, transcript: str, gender: str = 'unknown') -> dict:
    """
    오디오 버퍼와 텍스트를 기반으로 음성 점수를 계산합니다.
    """
    try:
        # 1. 음성 활동 감지 (VAD) - 오디오 버퍼의 RMS 에너지와 텍스트 길이 확인
        # librosa를 사용하여 RMS 에너지 계산
        rms_energy = librosa.feature.rms(y=audio_buffer, frame_length=2048, hop_length=512)[0]
        mean_rms_energy = np.mean(rms_energy)

        # 침묵 임계값 (조정 필요할 수 있음)
        SILENCE_THRESHOLD_RMS = 0.015 # 이 값은 테스트를 통해 최적화 필요

        if mean_rms_energy < SILENCE_THRESHOLD_RMS or not transcript.strip():
            print("음성 활동이 감지되지 않았거나 텍스트가 비어있습니다. 점수를 계산하지 않습니다.")
            return {
                'confidence_score': 0.0,
                'fluency_score': 0.0,
                'stability_score': 0.0,
                'clarity_score': 0.0,
                'overall_score': 0.0,
                'message': 'No speech detected or transcript is empty.'
            }

        acoustic_features = extract_prosodic_features_from_buffer(audio_buffer, sr)
        if not acoustic_features:
            print("음향 특성 추출에 실패했습니다.")
            return None

        word_count = len(transcript.split())
        duration_sec = acoustic_features.get("duration", 0)
        wpm = (word_count / duration_sec) * 60 if duration_sec > 0 else 0

        feature_data = {
            **acoustic_features,
            'transcript': transcript,
            'word_count': word_count,
            'wpm': wpm,
            'gender': gender,
        }

        feature_df = pd.DataFrame([feature_data])
        
        # 단일 파일용 점수 계산 함수 호출
        scores_df = calculate_scores_for_single_file(feature_df)
        if scores_df is None or scores_df.empty:
            print("점수 계산에 실패했습니다.")
            return None

        final_scores = scores_df.iloc[0].to_dict()
        print(f"음성 분석 점수 계산 완료: {final_scores}")
        return final_scores

    except Exception as e:
        print(f"음성 분석 서비스에서 오류 발생: {e}")
        traceback.print_exc()
        return None