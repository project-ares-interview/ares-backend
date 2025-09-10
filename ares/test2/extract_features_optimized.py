# -*- coding: utf-8 -*-
# AI Hub 면접 데이터 Feature 추출 스크립트 (최적화 버전)
# 1단계: 모든 prosodic features를 효율적으로 추출하여 CSV로 저장

import os
import json
import numpy as np
import pandas as pd
import parselmouth
import librosa
from pathlib import Path
import time

def optimized_jitter_shimmer(y, sr, f0_values):
    """최적화된 Jitter/Shimmer 계산"""
    try:
        if len(f0_values) < 3:
            return 0.01, 0.03, False
        
        # 1. 벡터화된 Jitter 계산
        periods = 1.0 / f0_values
        period_diffs = np.abs(np.diff(periods))
        jitter = np.mean(period_diffs) / np.mean(periods[:-1]) if np.mean(periods[:-1]) > 0 else 0.01
        
        # 2. Librosa RMS 활용한 Shimmer 계산
        rms = librosa.feature.rms(y=y, frame_length=int(sr*0.025), hop_length=int(sr*0.01))[0]
        if len(rms) > 1:
            rms_diffs = np.abs(np.diff(rms))
            shimmer = np.mean(rms_diffs) / np.mean(rms[:-1]) if np.mean(rms[:-1]) > 0 else 0.03
        else:
            shimmer = 0.03
        
        # 유효성 검사
        jitter = np.clip(jitter, 0.001, 1.0) if not np.isnan(jitter) else 0.01
        shimmer = np.clip(shimmer, 0.001, 1.0) if not np.isnan(shimmer) else 0.03
            
        return float(jitter), float(shimmer), True
        
    except Exception as e:
        print(f"    J/S 계산 실패: {e}")
        return 0.01, 0.03, False

def optimized_voiced_ratio(y, sr):
    """최적화된 복합 조건 기반 유성음 비율 계산"""
    try:
        # 한번에 모든 특성 계산 (hop_length 통일)
        hop_length = 512
        features = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
        energy = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop_length)[0]
        
        # 벡터화된 임계값 계산 및 조건 적용
        energy_thresh = np.percentile(energy, 20)
        zcr_thresh = np.percentile(zcr, 80)
        centroid_thresh = np.percentile(features, 50)
        
        # 복합 조건을 벡터 연산으로
        voiced_mask = (energy > energy_thresh) & (zcr < zcr_thresh) & (features < centroid_thresh)
        voiced_ratio = np.mean(voiced_mask)
        
        return float(voiced_ratio)
        
    except Exception as e:
        print(f"    Voiced ratio 계산 실패: {e}")
        return 0.7

def extract_prosodic_features_optimized(audio_path):
    """최적화된 단일 오디오 파일 prosodic features 추출"""
    try:
        print(f"🎵 특성 추출: {os.path.basename(audio_path)}")
        
        # 1. Parselmouth - 피치와 강도만 추출
        sound = parselmouth.Sound(audio_path)
        
        # 피치 분석
        pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=500)
        f0_values = pitch.selected_array['frequency']
        f0_values = f0_values[f0_values > 0]
        
        # 강도 분석
        intensity = sound.to_intensity(minimum_pitch=75)
        intensity_values = intensity.values.T.flatten()
        
        # 2. Librosa - 한번에 오디오 로드
        y, sr = librosa.load(audio_path, sr=16000)
        duration = len(y) / sr
        
        # 3. STFT 한번 계산 후 재사용
        S = np.abs(librosa.stft(y, hop_length=512))
        
        # 4. 스펙트럴 특성들을 STFT에서 계산
        frequencies = librosa.fft_frequencies(sr=sr)
        spectral_centroid = np.sum(frequencies[:, np.newaxis] * S, axis=0) / (np.sum(S, axis=0) + 1e-8)
        spectral_bandwidth = np.sqrt(np.sum(((frequencies[:, np.newaxis] - spectral_centroid) ** 2) * S, axis=0) / (np.sum(S, axis=0) + 1e-8))
        
        # 5. 나머지 특성들
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=512)[0]
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=512)
        
        # 6. 최적화된 수동 계산
        jitter, shimmer, js_success = optimized_jitter_shimmer(y, sr, f0_values)
        voiced_ratio = optimized_voiced_ratio(y, sr)
        
        return {
            # 파일 정보
            'file_name': os.path.basename(audio_path),
            
            # 피치 관련 (벡터화 연산)
            'f0_mean': float(np.mean(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_std': float(np.std(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_range': float(np.ptp(f0_values)) if len(f0_values) > 0 else 0.0,  # np.ptp = max-min
            'f0_median': float(np.median(f0_values)) if len(f0_values) > 0 else 0.0,
            'f0_count': len(f0_values),
            
            # 음성 품질
            'jitter': jitter,
            'shimmer': shimmer,
            'js_success': js_success,
            
            # 강도 (벡터화)
            'intensity_mean': float(np.mean(intensity_values)),
            'intensity_std': float(np.std(intensity_values)),
            'intensity_range': float(np.ptp(intensity_values)),
            
            # 스펙트럴 (STFT 재사용)
            'spectral_centroid_mean': float(np.mean(spectral_centroid)),
            'spectral_bandwidth_mean': float(np.mean(spectral_bandwidth)),
            'zcr_mean': float(np.mean(zcr)),
            
            # MFCC (벡터화)
            'mfcc_mean': float(np.mean(mfcc)),
            'mfcc_std': float(np.std(mfcc)),
            
            # 시간적 특성
            'duration': duration,
            'voiced_ratio': voiced_ratio
        }
        
    except Exception as e:
        print(f"❌ 특성 추출 실패 {os.path.basename(audio_path)}: {e}")
        return None

def load_metadata_from_json_fast(json_path):
    """최적화된 JSON 메타데이터 추출"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 경로 최적화 - 가장 가능성 높은 경로부터 시도
        answer = data.get('dataSet', {}).get('answer', {}).get('raw', {})
        answer_text = answer.get('text', "")
        word_count = answer.get('wordCount', 0)
        
        # duration 추출 최적화
        duration_ms = 0
        if 'rawDataInfo' in data and 'answer' in data['rawDataInfo']:
            duration_ms = data['rawDataInfo']['answer'].get('duration', 0)
        else:
            duration_ms = data.get('dataSet', {}).get('rawDataInfo', {}).get('answer', {}).get('duration', 0)
        
        # 메타데이터
        info = data.get('dataSet', {}).get('info', {})
        
        # WPM 계산 최적화
        duration_minutes = duration_ms / 60000 if duration_ms > 0 else 0
        wpm = word_count / duration_minutes if duration_minutes > 0 else 0
        
        return {
            'transcript': answer_text,
            'word_count': word_count,
            'duration_ms': duration_ms,
            'duration_sec': duration_ms / 1000,
            'wpm': wpm,
            'occupation': info.get('occupation', 'unknown'),
            'gender': info.get('gender', 'unknown'),
            'ageRange': info.get('ageRange', 'unknown'),
            'experience': info.get('experience', 'unknown')
        }
        
    except Exception as e:
        print(f"❌ JSON 읽기 실패 {os.path.basename(json_path)}: {e}")
        return {
            'transcript': "", 'word_count': 0, 'duration_ms': 0, 'duration_sec': 0, 'wpm': 0,
            'occupation': 'unknown', 'gender': 'unknown', 'ageRange': 'unknown', 'experience': 'unknown'
        }

class OptimizedFeatureExtractor:
    """최적화된 AI Hub 데이터 Feature 추출기"""
    
    def __init__(self, folder_path):
        self.folder_path = Path(folder_path)
        self.results = []
    
    def find_audio_json_pairs(self):
        """WAV-JSON 쌍 찾기 (캐싱 적용)"""
        wav_files = sorted(self.folder_path.glob("ckmk_a*.wav"))  # 정렬로 일관성 확보
        pairs = []
        
        for wav_path in wav_files:
            stem = wav_path.stem.replace("ckmk_a_", "")
            json_path = self.folder_path / f"ckmk_d_{stem}.json"
            
            if json_path.exists():
                pairs.append((wav_path, json_path))
            # 경고 메시지 줄임 (너무 많으면 로그 부담)
        
        print(f"📁 분석 대상: {len(pairs)}개 파일")
        return pairs
    
    def process_single_file(self, wav_path, json_path):
        """단일 파일 처리 최적화"""
        # 1. 오디오 특성 추출
        audio_features = extract_prosodic_features_optimized(str(wav_path))
        if not audio_features:
            return None
        
        # 2. 메타데이터 추출
        metadata = load_metadata_from_json_fast(json_path)
        
        # 3. 결합 (딕셔너리 merge 최적화)
        return {**audio_features, **metadata}
    
    def extract_all_features(self):
        """배치 특성 추출 최적화"""
        pairs = self.find_audio_json_pairs()
        
        print(f"🚀 {len(pairs)}개 파일 특성 추출 시작...")
        start_time = time.time()
        
        # 진행률 표시 최적화 (10개마다 -> 50개마다)
        for i, (wav_path, json_path) in enumerate(pairs):
            result = self.process_single_file(wav_path, json_path)
            if result:
                self.results.append(result)
            
            # 진행률 표시 간격 늘림
            if (i + 1) % 50 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed
                remaining = (len(pairs) - i - 1) / rate
                print(f"📊 진행률: {i+1}/{len(pairs)} ({(i+1)/len(pairs)*100:.1f}%) - {rate:.1f}파일/초, 예상잔여: {remaining/60:.1f}분")
        
        elapsed = time.time() - start_time
        print(f"✅ 특성 추출 완료: {len(self.results)}개 파일 - {elapsed:.1f}초 소요 ({len(self.results)/elapsed:.1f}파일/초)")
        
        return pd.DataFrame(self.results)
    
    def save_features(self, df, output_path):
        """특성을 CSV로 저장"""
        try:
            df.to_csv(output_path, index=False, encoding='utf-8-sig')
            print(f"💾 특성 저장 완료: {output_path}")
            print(f"📊 총 {len(df)}개 파일, {len(df.columns)}개 특성")
            
            # 기본 통계
            print(f"\n📈 추출된 특성 요약:")
            print(f"• 평균 음성 길이: {df['duration'].mean():.1f}초")
            print(f"• 평균 WPM: {df['wpm'].mean():.1f}")
            print(f"• Jitter/Shimmer 성공률: {df['js_success'].sum()}/{len(df)} ({df['js_success'].mean()*100:.1f}%)")
            
            # 성능 통계
            avg_jitter = df['jitter'].mean()
            avg_shimmer = df['shimmer'].mean()
            avg_voiced = df['voiced_ratio'].mean()
            print(f"• 평균 Jitter: {avg_jitter:.4f}, 평균 Shimmer: {avg_shimmer:.4f}")
            print(f"• 평균 Voiced Ratio: {avg_voiced:.3f}")
            
        except Exception as e:
            print(f"❌ 저장 실패: {e}")

def main():
    """메인 실행 함수"""
    print("🎯 AI Hub 면접 데이터 특성 추출 시작 (최적화 버전)")
    print("="*60)
    
    # 폴더 경로 (고정)
    folder_path = r"D:\면접data\129.채용면접 인터뷰 데이터\01-1.정식개방데이터\norm"
    output_csv = "extracted_features.csv"
    
    if not os.path.exists(folder_path):
        print(f"❌ 폴더가 존재하지 않습니다: {folder_path}")
        input("엔터 키를 눌러 종료...")
        return
    
    # 특성 추출
    extractor = OptimizedFeatureExtractor(folder_path)
    features_df = extractor.extract_all_features()
    
    if len(features_df) > 0:
        extractor.save_features(features_df, output_csv)
        print(f"\n🎉 특성 추출 완료!")
        print(f"📄 결과 파일: {output_csv}")
    else:
        print("❌ 추출된 특성이 없습니다.")
    
    input("엔터 키를 눌러 종료...")

if __name__ == "__main__":
    main()