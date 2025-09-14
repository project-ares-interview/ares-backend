# ares/api/services/voice_evaluation_service.py
import re
from typing import Dict, List

# 면접에서 일반적으로 사용되는 핵심 역량 키워드
# 실제 서비스에서는 직무별, 산업별로 더욱 정교화될 수 있습니다.
COMPETENCY_KEYWORDS: Dict[str, List[str]] = {
    "소통": ["소통", "경청", "설득", "협상", "공감"],
    "문제 해결": ["문제", "해결", "분석", "원인", "개선", "대안"],
    "주도성": ["주도", "적극", "먼저", "제안", "자발적"],
    "협업": ["협업", "팀워크", "함께", "공동", "동료"],
    "리더십": ["리더", "이끌", "방향", "목표", "조직"],
    "성실성": ["성실", "책임", "꾸준", "노력", "꼼꼼"],
}

def evaluate_speech(text: str, duration_sec: float) -> Dict:
    """
    인식된 텍스트와 발화 시간을 기반으로 언어적 지표를 평가합니다.

    Args:
        text (str): STT로 변환된 텍스트.
        duration_sec (float): 해당 텍스트의 발화 시간(초).

    Returns:
        Dict: WPM, 역량 키워드 분석 결과 등을 포함하는 딕셔너리.
    """
    if not text or duration_sec == 0:
        return {
            "wpm": 0,
            "competency_analysis": {key: 0 for key in COMPETENCY_KEYWORDS},
        }

    # 1. WPM (Words Per Minute) 계산
    # 한국어는 공백 기준으로 단어를 계산하는 것이 정확하지 않을 수 있으나,
    # 실시간 지표로서는 유의미한 기준을 제공합니다.
    word_count = len(text.split())
    wpm = (word_count / duration_sec) * 60 if duration_sec > 0 else 0

    # 2. 역량 키워드 분석
    competency_analysis = {}
    for competency, keywords in COMPETENCY_KEYWORDS.items():
        count = 0
        for keyword in keywords:
            # 정규표현식을 사용하여 단어 단위로 키워드를 찾습니다.
            count += len(re.findall(rf'\b{keyword}\b', text, re.IGNORECASE))
        competency_analysis[competency] = count

    return {
        "wpm": round(wpm),
        "competency_analysis": competency_analysis,
    }
