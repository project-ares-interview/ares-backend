# ares/api/utils/audio_utils.py
import numpy as np
import parselmouth
import librosa
import os

def optimized_jitter_shimmer(y, sr, f0_values):
    """최적화된 Jitter/Shimmer 계산"""
    try:
        if len(f0_values) < 3:
            return 0.01, 0.03, False
        
        periods = 1.0 / f0_values
        period_diffs = np.abs(np.diff(periods))
        jitter = np.mean(period_diffs) / np.mean(periods[:-1]) if np.mean(periods[:-1]) > 0 else 0.01
        
        rms = librosa.feature.rms(y=y, frame_length=int(sr*0.025), hop_length=int(sr*0.01))[0]
        if len(rms) > 1:
            rms_diffs = np.abs(np.diff(rms))
            shimmer = np.mean(rms_diffs) / np.mean(rms[:-1]) if np.mean(rms[:-1]) > 0 else 0.03
        else:
            shimmer = 0.03
        
        jitter = np.clip(jitter, 0.001, 1.0) if not np.isnan(jitter) else 0.01
        shimmer = np.clip(shimmer, 0.001, 1.0) if not np.isnan(shimmer) else 0.03
            
        return float(jitter), float(shimmer), True
        
    except Exception:
        return 0.01, 0.03, False

def optimized_voiced_ratio(y, sr):
    """최적화된 복합 조건 기반 유성음 비율 계산"""
    try:
        hop_length = 512
        features = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
        energy = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop_length)[0]
        
        energy_thresh = np.percentile(energy, 20)
        zcr_thresh = np.percentile(zcr, 80)
        centroid_thresh = np.percentile(features, 50)
        
        voiced_mask = (energy > energy_thresh) & (zcr < zcr_thresh) & (features < centroid_thresh)
        voiced_ratio = np.mean(voiced_mask)
        
        return float(voiced_ratio)
        
    except Exception:
        return 0.7

def extract_prosodic_features_optimized(audio_path):
    """최적화된 단일 오디오 파일 prosodic features 추출"""
    try:
        sound = parselmouth.Sound(audio_path)
        pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=500)
        f0_values = pitch.selected_array['frequency']
        f0_values = f0_values[f0_values > 0]
        
        intensity = sound.to_intensity(minimum_pitch=75)
        intensity_values = intensity.values.T.flatten()
        
        y, sr = librosa.load(audio_path, sr=16000)
        duration = len(y) / sr
        
        S = np.abs(librosa.stft(y, hop_length=512))
        
        frequencies = librosa.fft_frequencies(sr=sr)
        spectral_centroid = np.sum(frequencies[:, np.newaxis] * S, axis=0) / (np.sum(S, axis=0) + 1e-8)
        spectral_bandwidth = np.sqrt(np.sum(((frequencies[:, np.newaxis] - spectral_centroid) ** 2) * S, axis=0) / (np.sum(S, axis=0) + 1e-8))
        
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=512)[0]
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=512)
        
        jitter, shimmer, js_success = optimized_jitter_shimmer(y, sr, f0_values)
        voiced_ratio = optimized_voiced_ratio(y, sr)
        
        return {
            'file_name': os.path.basename(audio_path),
            'f0_mean': float(np.mean(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_std': float(np.std(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_range': float(np.ptp(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_median': float(np.median(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_count': len(f0_values),
            'jitter': jitter,
            'shimmer': shimmer,
            'js_success': js_success,
            'intensity_mean': float(np.mean(intensity_values)),
            'intensity_std': float(np.std(intensity_values)),
            'intensity_range': float(np.ptp(intensity_values)),
            'spectral_centroid_mean': float(np.mean(spectral_centroid)),
            'spectral_bandwidth_mean': float(np.mean(spectral_bandwidth)),
            'zcr_mean': float(np.mean(zcr)),
            'mfcc_mean': float(np.mean(mfcc)),
            'mfcc_std': float(np.std(mfcc)),
            'duration': duration,
            'voiced_ratio': voiced_ratio
        }
        
    except Exception as e:
        print(f"❌ 특성 추출 실패 {os.path.basename(audio_path)}: {e}")
        return None
