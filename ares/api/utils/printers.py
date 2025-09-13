# printers.py
from ares.api.utils.utils_fw import _ensure_list, _parse_fw_token
from ares.api.services.scoring import compute_total_from_scores, normalize_scores

def _fw_label(framework, signal):
    return f"{framework}+{signal}" if signal else framework

def print_framework_scores_from_selected(analysis: dict, framework_token: str, title: str):
    """지정된 프레임워크 토큰 하나에 대한 점수를 계산하고, 출력하며, 결과를 반환하는 함수"""
    scores = analysis.get("scores", {})
    if not scores:
        return None # 점수가 없으면 None 반환

    # ... (기존의 점수 계산 로직은 모두 동일) ...
    parts = framework_token.upper().split('+')
    base_fw = parts[0]
    signal = parts[1] if len(parts) > 1 else None
    
    total, base_scores, sig_key, sig_score = compute_total_from_scores(base_fw, signal, scores)

    # 결과 출력
    line = "-" * 40
    print(f"\n{line}\n✅ {title}\n{line}")
    for key, score in base_scores.items():
        print(f"  - {key.capitalize()}: {score}/20점")
    if sig_key:
        print(f"  - {sig_key.capitalize()}: {sig_score}/10점")
    
    # <<<<<<< 🌟 핵심 수정: 계산된 결과를 딕셔너리로 묶어서 반환 >>>>>>>>>
    return {
        "total": total,
        "base_scores": base_scores,
        "signal_key": sig_key,
        "signal_score": sig_score
    }
