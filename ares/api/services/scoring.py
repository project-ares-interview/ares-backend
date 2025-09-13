_BASE_KEYS = {
    "star": ["situation", "task", "action", "result"],
    "competency": ["competency", "behavior", "impact"],
    "case": ["problem", "structure", "analysis", "recommendation"],
    "systemdesign": ["requirements", "tradeoffs", "architecture", "risks"],
}

_SIGNAL_KEYS_MAP = {"c": "challenge", "l": "learning", "m": "metrics"}

# <<<<<<< 🌟 핵심 수정: 모든 변형을 '소문자 표준 키'로 매핑 >>>>>>>>>
_KEY_NORMALIZATION_MAP = {
    # 표준 키 -> 자기 자신 (소문자)
    "situation": "situation", "task": "task", "action": "action", "result": "result",
    "competency": "competency", "behavior": "behavior", "impact": "impact",
    "problem": "problem", "structure": "structure", "analysis": "analysis", "recommendation": "recommendation",
    "requirements": "requirements", "tradeoffs": "tradeoffs", "architecture": "architecture", "risks": "risks",
    "challenge": "challenge", "learning": "learning", "metrics": "metrics",

    # 약어, 변형, 대문자 -> 소문자 표준 키
    's': 'situation', 't': 'task', 'a': 'action', 'r': 'result',
    'c': 'competency', 'b': 'behavior', 'i': 'impact', # Competency 약어 추가 (스크린샷 기준)
    'p': 'problem',
    'challenge': 'challenge', 'learning': 'learning', 'metrics': 'metrics',

    # AI가 보낼 수 있는 다른 변형들
    'star': 'star', 'base': 'competency', 'competency-based': 'competency',
    'case': 'case/mece', 'mece': 'case/mece', 'Case' : 'case/mece', 
    'system': 'systemdesign',

    # 오타
    'stucture': 'structure',
}


# --- 유틸리티 함수 ---

def _safe_int(x):
    try:
        return int(x)
    except (ValueError, TypeError):
        try:
            return int(float(x))
        except (ValueError, TypeError):
            return 0

# <<<< 핵심 변경 사항 2: 약어와 오타를 모두 처리하는 새로운 정규화 함수 >>>>
def normalize_scores(scores: dict) -> dict:
    """
    AI가 반환한 scores 딕셔너리의 키(약어, 오타 등)를
    'situation'과 같은 표준 키로 변환한 새 딕셔너리를 반환합니다.
    """
    if not isinstance(scores, dict):
        return {}
    
    normalized = {}
    for key, value in scores.items():
        # 키를 소문자로 변환하여 매핑 테이블에서 표준 키를 찾음
        standard_key = _KEY_NORMALIZATION_MAP.get(str(key).lower())
        if standard_key:
            normalized[standard_key] = value
            
    return normalized


# --- 메인 계산 함수 ---

def compute_total_from_scores(framework: str, signal: str | None, scores: dict):
    """모든 키를 소문자로 정규화한 후 프레임워크에 맞는 점수를 계산합니다."""
    
    # AI가 보낸 scores의 모든 키를 소문자 표준 키로 변환
    normalized_scores = { _KEY_NORMALIZATION_MAP.get(str(k).lower(), str(k).lower()): v for k, v in scores.items() }

    # 프레임워크 이름 자체도 소문자 표준 키로 변환
    normalized_framework = _KEY_NORMALIZATION_MAP.get(framework.lower(), framework.lower())
    
    base_keys = _BASE_KEYS.get(normalized_framework, [])
    base_scores = {k: _safe_int(normalized_scores.get(k, 0)) for k in base_keys}
    base_sum = sum(base_scores.values())

    sig_key = _SIGNAL_KEYS_MAP.get((signal or "").lower(), None)
    sig_score = _safe_int(normalized_scores.get(sig_key, 0)) if sig_key else 0

    return base_sum + sig_score, base_scores, sig_key, sig_score