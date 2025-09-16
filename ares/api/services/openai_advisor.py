# openai_advisor.py - Azure OpenAI 연동 면접 조언 시스템
import os
import json
import requests
from datetime import datetime
import time
from django.conf import settings

class InterviewAdvisor:
    """과학적 근거 기반 AI 면접 조언 시스템"""
    
    def __init__(self, api_key=None, endpoint=None, deployment_name=None, api_version=None):
        """
        Azure OpenAI 클라이언트 초기화
        """
        self.api_key = api_key or getattr(settings, 'AZURE_OPENAI_API_KEY', None)
        self.endpoint = endpoint or getattr(settings, 'AZURE_OPENAI_ENDPOINT', None)
        self.deployment_name = deployment_name or getattr(settings, 'AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o-mini')
        self.api_version = api_version or getattr(settings, 'AZURE_OPENAI_API_VERSION', '2024-02-01')
        
        if not self.api_key or not self.endpoint:
            print("⚠️ Azure OpenAI 인증 정보가 설정되지 않았습니다.")
        
        self.scientific_standards = {
            'blink_rate': {
                'normal_range': (12, 20), 'stress_indicator': 25, 'source': 'Psychological Science, 2018'
            },
            'smile_percentage': {
                'optimal_range': (25, 40), 'minimal_threshold': 15, 'source': 'Journal of Business Psychology, 2019'
            },
            'gesture_frequency': {
                'optimal_range': (2, 8), 'excessive_threshold': 12, 'source': 'Communication Research, 2020'
            },
            'posture_stability': {
                'good_threshold': 80, 'poor_threshold': 50, 'source': 'Body Language in Business, 2021'
            },
            'head_movements': {
                'optimal_nods': (3, 8), 'excessive_nods': 15, 'excessive_shakes': 5, 'source': 'Nonverbal Communication Studies, 2022'
            }
        }

    def generate_advice(self, analysis_data):
        try:
            system_prompt = self._create_system_prompt()
            user_prompt = self._create_user_prompt(analysis_data)
            
            if user_prompt is None:
                 return {
                    'status': 'error',
                    'message': 'Not enough data to generate advice.',
                    'fallback_advice': '분석 데이터가 부족하여 조언을 생성할 수 없습니다.'
                }

            response = self._call_azure_openai(system_prompt, user_prompt)
            
            if response:
                return {
                    'status': 'success',
                    'advice': response,
                    'timestamp': datetime.now().isoformat(),
                    'analysis_summary': self._create_analysis_summary(analysis_data)
                }
            else:
                return {
                    'status': 'error',
                    'message': 'AI 조언 생성에 실패했습니다.',
                    'fallback_advice': self._generate_fallback_advice(analysis_data)
                }
                
        except Exception as e:
            print(f"조언 생성 오류: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'fallback_advice': self._generate_fallback_advice(analysis_data)
            }

    def _create_system_prompt(self):
        return f"""당신은 과학적 근거를 바탕으로 면접 조언을 제공하는 전문 면접 코치입니다.

**역할**: 비언어적 행동 분석 전문가 및 면접 코치

**전문성 기준**:
- 심리학 및 커뮤니케이션 연구 기반 분석
- 정량적 데이터 해석 능력
- 실용적이고 구체적인 개선 방안 제시

**과학적 근거 데이터베이스**:
{json.dumps(self.scientific_standards, indent=2, ensure_ascii=False)}

**조언 원칙**:
1. 객관적 데이터에 기반한 분석
2. 긍정적이고 건설적인 피드백
3. 구체적이고 실행 가능한 개선 방안 (각 개선 영역별로 명확한 지시 포함)
4. 과학적 근거와 함께 설명
5. 개인의 자존감을 해치지 않는 표현

**각 점수 항목별 조언 지침**:
- **자신감 점수**:
    - **음성 강도**: 성별 표준에 맞는 적절한 음성 강도를 유지하도록 조언. 너무 작거나 크게 말하는 것을 피하도록 지시.
    - **피치 안정성 (f0_cv)**: 안정적인 피치를 유지하도록 조언. 과도한 피치 변화는 긴장감을 나타낼 수 있으므로 피하도록 지시.
    - **음성 품질 (Jitter, Shimmer)**: 부드럽고 일관된 음성 품질을 목표로 하도록 조언. 음성 떨림이나 거친 소리를 줄이도록 지시.
- **유창성 점수**:
    - **분당 단어 수 (WPM)**: 분당 160단어 정도의 적절한 말하기 속도를 유지하도록 조언. 너무 빠르거나 느리게 말하는 것을 피하도록 지시.
    - **유성음 비율 (Voiced Ratio)**: 유성음의 균형을 잘 유지하도록 조언. 과도한 일시 정지나 무성음(예: "음", "어") 사용을 피하도록 지시.
    - **스펙트럼 안정성 (ZCR)**: 안정적인 스펙트럼 품질을 유지하도록 조언. 소리 특성의 갑작스러운 변화를 피하도록 지시.
- **안정성 점수**:
    - **피치 안정성 (f0_cv)**: 말하는 내내 일관되고 안정적인 피치를 유지하도록 조언.
    - **음성 강도 변동 (Intensity CV)**: 일관된 음성 볼륨을 유지하도록 조언. 갑작스러운 소리의 크기 감소 또는 증가를 피하도록 지시.
- **명료성 점수**:
    - **스펙트럼 중심 (Spectral Centroid)**: 명료성을 위해 음성 공명이 일반적인 말하기 패턴과 일치하는지 확인하도록 조언.
    - **스펙트럼 대역폭 (Spectral Bandwidth)**: 명확하고 집중된 음성 품질을 유지하도록 조언. 뭉개지거나 지나치게 넓은 발성을 피하도록 지시.
    - **음색 일관성 (MFCC Std)**: 일관된 음색과 톤을 유지하도록 조언. 음성 품질의 큰 변화를 피하도록 지시.

**출력 형식**:
- 📊 전체 평가: 종합 점수 (A+/A/B+/B/C+/C/D)
- 🎯 주요 강점: 2-3가지
- ⚠️ 개선 영역: 2-3가지 (우선순위 순, 각 영역별로 점수를 높이기 위한 구체적인 행동 지침을 상세히 설명)
- 📚 과학적 근거: 관련 연구나 통계 인용

한국어로 친근하고 전문적인 톤으로 답변하세요.
"""

    def _create_user_prompt(self, analysis_data):
        try:
            video_analysis = analysis_data.get('video_analysis', {})
            behavioral_metrics = video_analysis.get('behavioral_metrics', {})
            
            # The temp file's data structure seems to have these nested.
            # Let's check if the necessary keys exist.
            if not all(k in behavioral_metrics for k in ['eye_contact', 'facial_expressions', 'head_movements', 'posture', 'hand_gestures']):
                return None

            summary = f"""
면접 분석 데이터

음성 분석:
- 자신감: {analysis_data.get('voice_analysis', {}).get('confidence_score', 'N/A')}점
- 유창성: {analysis_data.get('voice_analysis', {}).get('fluency_score', 'N/A')}점
- 안정성: {analysis_data.get('voice_analysis', {}).get('stability_score', 'N/A')}점
- 명료성: {analysis_data.get('voice_analysis', {}).get('clarity_score', 'N/A')}점

행동 분석 (영상):
- 눈 깜빡임률: {behavioral_metrics['eye_contact']['blink_rate_per_minute']}회/분 (정상: 12-20)
- 미소 비율: {behavioral_metrics['facial_expressions']['smile_percentage']}% (권장: 25-40)
- 머리 안정성: {behavioral_metrics['head_movements']['head_stability_score']}/100점
- 자세 안정성: {behavioral_metrics['posture']['stability_score']}/100점
- 손 제스쳐 빈도: {behavioral_metrics['hand_gestures']['gesture_frequency_per_minute']}회/분 (권장: 2-8)

위 데이터를 종합하여 이 면접자의 비언어적 커뮤니케이션에 대한 전문적인 분석과 조언을 제공해주세요.
특히 각 지표가 과학적 기준과 비교했을 때 어떤 의미인지, 그리고 구체적으로 어떻게 개선할 수 있는지 알려주세요.
"""
            return summary
        except KeyError as e:
            print(f"Data for prompt creation is missing key: {e}")
            return None

    def _call_azure_openai(self, system_prompt, user_prompt):
        if not self.api_key or not self.endpoint:
            print("❌ Azure OpenAI 인증 정보가 없어 API를 호출할 수 없습니다.")
            return None
        
        url = f"{self.endpoint.rstrip('/')}/openai/deployments/{self.deployment_name}/chat/completions?api-version={self.api_version}"
        headers = { 'Content-Type': 'application/json', 'api-key': self.api_key }
        data = {
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ],
            'max_tokens': 2000, 'temperature': 0.7, 'top_p': 0.95,
            'frequency_penalty': 0.1, 'presence_penalty': 0.1
        }
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            else:
                print("❌ 응답 형식이 올바르지 않습니다.")
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ API 호출 오류: {e}")
            return None
        except Exception as e:
            print(f"❌ 응답 처리 오류: {e}")
            return None

    def _generate_fallback_advice(self, analysis_data):
        advice_parts = []
        try:
            video_metrics = analysis_data.get('video_analysis', {}).get('behavioral_metrics', {})
            if not video_metrics:
                return "영상 분석 데이터가 없어 기본 조언을 생성할 수 없습니다."

            blink_rate = video_metrics.get('eye_contact', {}).get('blink_rate_per_minute', 0)
            if blink_rate > 25:
                advice_parts.append("👁 **눈 깜빡임**: 분당 25회 이상으로 긴장 상태를 나타냅니다. 심호흡으로 긴장을 완화하세요.")
            elif blink_rate > 0 and blink_rate < 12:
                advice_parts.append("👁 **눈 깜빡임**: 너무 적어 경직되어 보일 수 있습니다. 자연스럽게 깜빡이세요.")
            
            smile_percentage = video_metrics.get('facial_expressions', {}).get('smile_percentage', 0)
            if smile_percentage < 15:
                advice_parts.append("😊 **미소**: 전체 시간의 15% 미만으로 경직된 인상입니다. 적절한 미소로 친근함을 표현하세요.")
            
            gesture_freq = video_metrics.get('hand_gestures', {}).get('gesture_frequency_per_minute', 0)
            if gesture_freq > 12:
                advice_parts.append("👋 **손 제스쳐**: 분당 12회 이상으로 과도합니다. 차분한 손동작을 연습하세요.")
            elif gesture_freq > 0 and gesture_freq < 2:
                advice_parts.append("👋 **손 제스쳐**: 제스쳐가 부족해 경직되어 보입니다. 적절한 손동작으로 표현력을 높이세요.")
            
            posture_score = video_metrics.get('posture', {}).get('stability_score', 100)
            if posture_score < 50:
                advice_parts.append("📱 **자세**: 불안정합니다. 어깨를 편안히 하고 등을 곧게 펴세요.")
            
            if not advice_parts:
                advice_parts.append("✅ **종합**: 전반적으로 양호한 면접 태도를 보여줍니다!")
            
            return "\n\n".join(advice_parts)
        except Exception:
            return "기본 조언 생성 중 오류가 발생했습니다."

    def _create_analysis_summary(self, analysis_data):
        return {}

# Singleton instance
advisor = InterviewAdvisor()
