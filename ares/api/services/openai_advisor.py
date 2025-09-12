# ares/api/services/openai_advisor.py
import os
import json
import requests
from datetime import datetime
import time

from ares.api.config import AI_CONFIG

class InterviewAdvisor:
    """과학적 근거 기반 AI 면접 조언 시스템"""
    
    def __init__(self, api_key=None, endpoint=None, deployment_name=None, api_version=None):
        """
        Azure OpenAI 클라이언트 초기화
        
        Args:
            api_key: Azure OpenAI API 키
            endpoint: Azure OpenAI 엔드포인트 URL
            deployment_name: 배포된 모델 이름
            api_version: API 버전
        """
        self.api_key = api_key or os.getenv('AZURE_OPENAI_KEY')
        self.endpoint = endpoint or os.getenv('AZURE_OPENAI_ENDPOINT')
        self.deployment_name = deployment_name or AI_CONFIG.get("CHAT_DEPLOYMENT")
        self.api_version = api_version or AI_CONFIG.get("API_VERSION")
        
        if not self.api_key or not self.endpoint:
            print("⚠️ Azure OpenAI 인증 정보가 설정되지 않았습니다.")
            print("환경 변수(.env)에 다음을 설정해주세요:")
            print("- AZURE_OPENAI_KEY")
            print("- AZURE_OPENAI_ENDPOINT")
        
        # 과학적 근거 데이터베이스
        self.scientific_standards = {
            'blink_rate': {
                'normal_range': (12, 20),  # 분당 12-20회
                'stress_indicator': 25,     # 분당 25회 이상시 스트레스
                'source': 'Psychological Science, 2018'
            },
            'smile_percentage': {
                'optimal_range': (25, 40),  # 전체 시간의 25-40%
                'minimal_threshold': 15,    # 15% 미만시 경직된 인상
                'source': 'Journal of Business Psychology, 2019'
            },
            'gesture_frequency': {
                'optimal_range': (2, 8),    # 분당 2-8회
                'excessive_threshold': 12,   # 분당 12회 이상시 과도함
                'source': 'Communication Research, 2020'
            },
            'posture_stability': {
                'good_threshold': 80,       # 80점 이상 양호
                'poor_threshold': 50,       # 50점 미만 불안정
                'source': 'Body Language in Business, 2021'
            },
            'head_movements': {
                'optimal_nods': (3, 8),     # 3-8회 적절한 끄덕임
                'excessive_nods': 15,       # 15회 이상 과도함
                'excessive_shakes': 5,      # 5회 이상 부정적 신호
                'source': 'Nonverbal Communication Studies, 2022'
            }
        }

    def generate_advice(self, analysis_data):
        """
        분석 데이터를 바탕으로 면접 조언 생성
        
        Args:
            analysis_data: 면접 분석 데이터
            
        Returns:
            dict: 조언 결과
        """
        try:
            # 시스템 프롬프트 생성
            system_prompt = self._create_system_prompt()
            
            # 사용자 프롬프트 생성
            user_prompt = self._create_user_prompt(analysis_data)
            
            # Azure OpenAI API 호출
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
        """AI 시스템을 위한 전문가 프롬프트 생성"""
        return f'''당신은 과학적 근거를 바탕으로 면접 조언을 제공하는 전문 면접 코치입니다.

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
3. 구체적이고 실행 가능한 개선 방안
4. 과학적 근거와 함께 설명
5. 개인의 자존감을 해치지 않는 표현

**출력 형식**:
- 📊 **전체 평가**: 종합 점수 (A+/A/B+/B/C+/C/D)
- 🎯 **주요 강점**: 2-3가지
- ⚠️ **개선 영역**: 2-3가지 (우선순위 순)
- 💡 **구체적 개선 방안**: 실행 가능한 팁들
- 📚 **과학적 근거**: 관련 연구나 통계 인용

한국어로 친근하고 전문적인 톤으로 답변하세요.'''

    def _create_user_prompt(self, analysis_data):
        """분석 데이터를 바탕으로 사용자 프롬프트 생성"""
        session_info = analysis_data['session_info']
        behavioral_metrics = analysis_data['behavioral_metrics']
        standards = analysis_data['scientific_standards']
        
        # 주요 지표 요약
        summary = f'''
**면접 분석 데이터**

**세션 정보**:
- 분석 시간: {session_info['duration_seconds']}초 ({session_info['duration_seconds']/60:.1f}분)
- 총 프레임 수: {session_info['total_frames']}개

**눈 깜빡임 분석**:
- 총 깜빡임: {behavioral_metrics['eye_contact']['blink_count']}회
- 깜빡임률: {behavioral_metrics['eye_contact']['blink_rate_per_minute']}회/분
- 정상 범위: {standards['normal_blink_rate_range']['min']}-{standards['normal_blink_rate_range']['max']}회/분

**표정 및 미소 분석**:
- 총 미소 시간: {behavioral_metrics['facial_expressions']['total_smile_time']}초
- 미소 비율: {behavioral_metrics['facial_expressions']['smile_percentage']}%
- 권장 범위: {standards['optimal_smile_percentage_range']['min']}-{standards['optimal_smile_percentage_range']['max']}%
- 미세 표정 변화: {behavioral_metrics['facial_expressions']['micro_expressions']}회

**머리 움직임 분석**:
- 끄덕임: {behavioral_metrics['head_movements']['nod_count']}회
- 좌우 흔들기: {behavioral_metrics['head_movements']['shake_count']}회
- 머리 안정성: {behavioral_metrics['head_movements']['head_stability_score']}/100점

**자세 분석**:
- 자세 흔들림: {behavioral_metrics['posture']['sway_count']}회
- 자세 안정성: {behavioral_metrics['posture']['stability_score']}/100점

**손 제스쳐 분석**:
- 총 제스쳐: {behavioral_metrics['hand_gestures']['gesture_count']}회
- 제스쳐 빈도: {behavioral_metrics['hand_gestures']['gesture_frequency_per_minute']}회/분
- 손 노출 시간: {behavioral_metrics['hand_gestures']['hands_visible_seconds']}초
- 권장 범위: {standards['appropriate_gesture_frequency_range']['min']}-{standards['appropriate_gesture_frequency_range']['max']}회/분

위 데이터를 종합하여 이 면접자의 비언어적 커뮤니케이션에 대한 전문적인 분석과 조언을 제공해주세요.
특히 각 지표가 과학적 기준과 비교했을 때 어떤 의미인지, 그리고 구체적으로 어떻게 개선할 수 있는지 알려주세요.
'''
        return summary

    def _call_azure_openai(self, system_prompt, user_prompt):
        """Azure OpenAI API 호출"""
        if not self.api_key or not self.endpoint:
            print("❌ Azure OpenAI 인증 정보가 없어 API를 호출할 수 없습니다.")
            return None
        
        url = f"{self.endpoint}/openai/deployments/{self.deployment_name}/chat/completions?api-version={self.api_version}"
        
        headers = {
            'Content-Type': 'application/json',
            'api-key': self.api_key
        }
        
        data = {
            'messages': [
                {
                    'role': 'system',
                    'content': system_prompt
                },
                {
                    'role': 'user', 
                    'content': user_prompt
                }
            ],
            'max_tokens': 2000,
            'temperature': 0.7,
            'top_p': 0.95,
            'frequency_penalty': 0.1,
            'presence_penalty': 0.1
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

    def _create_analysis_summary(self, analysis_data):
        """분석 데이터 요약 생성"""
        behavioral = analysis_data['behavioral_metrics']
        duration_min = analysis_data['session_info']['duration_seconds'] / 60
        
        return {
            'session_duration_minutes': round(duration_min, 1),
            'blink_rate_status': self._assess_blink_rate(behavioral['eye_contact']['blink_rate_per_minute']),
            'smile_status': self._assess_smile_percentage(behavioral['facial_expressions']['smile_percentage']),
            'gesture_status': self._assess_gesture_frequency(behavioral['hand_gestures']['gesture_frequency_per_minute']),
            'posture_status': self._assess_posture_stability(behavioral['posture']['stability_score']),
            'head_movement_status': self._assess_head_movements(behavioral['head_movements']['nod_count'], behavioral['head_movements']['shake_count'])
        }

    def _assess_blink_rate(self, rate):
        """깜빡임률 평가"""
        normal_min, normal_max = self.scientific_standards['blink_rate']['normal_range']
        
        if rate < normal_min:
            return {'level': 'low', 'description': '깜빡임이 적음 (집중 또는 긴장)'}
        elif rate > normal_max:
            if rate > self.scientific_standards['blink_rate']['stress_indicator']:
                return {'level': 'high', 'description': '깜빡임 과다 (스트레스 신호)'}
            return {'level': 'slightly_high', 'description': '약간 많은 깜빡임'}
        else:
            return {'level': 'normal', 'description': '정상 범위'}

    def _assess_smile_percentage(self, percentage):
        """미소 비율 평가"""
        optimal_min, optimal_max = self.scientific_standards['smile_percentage']['optimal_range']
        minimal = self.scientific_standards['smile_percentage']['minimal_threshold']
        
        if percentage < minimal:
            return {'level': 'too_low', 'description': '미소가 부족함 (경직된 인상)'}
        elif percentage < optimal_min:
            return {'level': 'low', 'description': '미소가 적음'}
        elif percentage > optimal_max:
            return {'level': 'high', 'description': '미소가 많음 (긍정적)'}
        else:
            return {'level': 'optimal', 'description': '적절한 미소'}

    def _assess_gesture_frequency(self, frequency):
        """제스쳐 빈도 평가"""
        optimal_min, optimal_max = self.scientific_standards['gesture_frequency']['optimal_range']
        excessive = self.scientific_standards['gesture_frequency']['excessive_threshold']
        
        if frequency == 0:
            return {'level': 'none', 'description': '제스쳐 없음 (경직됨)'}
        elif frequency < optimal_min:
            return {'level': 'low', 'description': '제스쳐 부족'}
        elif frequency > excessive:
            return {'level': 'excessive', 'description': '제스쳐 과다 (산만함)'}
        elif frequency > optimal_max:
            return {'level': 'high', 'description': '제스쳐 많음'}
        else:
            return {'level': 'optimal', 'description': '적절한 제스쳐'}

    def _assess_posture_stability(self, stability_score):
        """자세 안정성 평가"""
        good_threshold = self.scientific_standards['posture_stability']['good_threshold']
        poor_threshold = self.scientific_standards['posture_stability']['poor_threshold']
        
        if stability_score >= good_threshold:
            return {'level': 'good', 'description': '안정적인 자세'}
        elif stability_score >= poor_threshold:
            return {'level': 'moderate', 'description': '보통 자세'}
        else:
            return {'level': 'poor', 'description': '불안정한 자세'}

    def _assess_head_movements(self, nods, shakes):
        """머리 움직임 평가"""
        optimal_nod_min, optimal_nod_max = self.scientific_standards['head_movements']['optimal_nods']
        excessive_nods = self.scientific_standards['head_movements']['excessive_nods']
        excessive_shakes = self.scientific_standards['head_movements']['excessive_shakes']
        
        issues = []
        
        if nods < optimal_nod_min:
            issues.append('끄덕임 부족')
        elif nods > excessive_nods:
            issues.append('과도한 끄덕임')
            
        if shakes >= excessive_shakes:
            issues.append('좌우 흔들기 과다')
            
        if not issues:
            return {'level': 'good', 'description': '적절한 머리 움직임'}
        else:
            return {'level': 'needs_improvement', 'description': ', '.join(issues)}

    def _generate_fallback_advice(self, analysis_data):
        """AI 호출 실패시 기본 조언 생성"""
        behavioral = analysis_data['behavioral_metrics']
        advice_parts = []
        
        # 깜빡임 분석
        blink_rate = behavioral['eye_contact']['blink_rate_per_minute']
        if blink_rate > 25:
            advice_parts.append("👁 **눈 깜빡임**: 분당 25회 이상으로 긴장 상태를 나타냅니다. 심호흡으로 긴장을 완화하세요.")
        elif blink_rate < 12:
            advice_parts.append("👁 **눈 깜빡임**: 너무 적어 경직되어 보일 수 있습니다. 자연스럽게 깜빡이세요.")
        
        # 미소 분석
        smile_percentage = behavioral['facial_expressions']['smile_percentage']
        if smile_percentage < 15:
            advice_parts.append("😊 **미소**: 전체 시간의 15% 미만으로 경직된 인상입니다. 적절한 미소로 친근함을 표현하세요.")
        elif smile_percentage > 40:
            advice_parts.append("😊 **미소**: 매우 긍정적입니다! 현재의 밝은 표정을 유지하세요.")
            
        # 제스쳐 분석
        gesture_freq = behavioral['hand_gestures']['gesture_frequency_per_minute']
        if gesture_freq > 12:
            advice_parts.append("👋 **손 제스쳐**: 분당 12회 이상으로 과도합니다. 차분한 손동작을 연습하세요.")
        elif gesture_freq < 2:
            advice_parts.append("👋 **손 제스쳐**: 제스쳐가 부족해 경직되어 보입니다. 적절한 손동작으로 표현력을 높이세요.")
            
        # 자세 분석
        posture_score = behavioral['posture']['stability_score']
        if posture_score < 50:
            advice_parts.append("📱 **자세**: 불안정합니다. 어깨를 편안히 하고 등을 곧게 펴세요.")
            
        if not advice_parts:
            advice_parts.append("✅ **종합**: 전반적으로 양호한 면접 태도를 보여줍니다!")
            
        return "\n\n".join(advice_parts)