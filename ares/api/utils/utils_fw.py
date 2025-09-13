# === utils_fw.py 같은 곳에 두거나 파일 상단에 추가 ===
def _ensure_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

def _parse_fw_token(token):
    """
    token 지원 형식:
      - "STAR+M" 같은 문자열
      - {"framework":"STAR","signal":"M"} 같은 dict
      - ["STAR+M"] 같이 1원소 리스트가 섞여 들어오는 경우
    반환: (framework:str, signal:str|None)
    """
    if isinstance(token, dict):
        fw = (token.get("framework") or "").strip()
        sig = token.get("signal")
        if isinstance(sig, str):
            sig = sig.strip() or None
        return fw, sig

    if isinstance(token, str):
        parts = [p.strip() for p in token.split("+")]
        fw = parts[0] if parts else ""
        sig = parts[1] if len(parts) > 1 else None
        return fw, sig

    if isinstance(token, list) and len(token) == 1:
        return _parse_fw_token(token[0])

    raise TypeError(f"Unsupported framework token type: {type(token)}")
