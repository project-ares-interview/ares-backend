# ares/api/utils/text_utils.py
import re
from typing import List

def safe_strip(s: str) -> str:
    return (s or "").strip()

def normalize_lines(text: str) -> List[str]:
    lines = []
    for raw in (text or "").splitlines():
        l = raw.strip()
        if not l:
            continue
        l = re.sub(r"^[\-•\d\.\)\(\]+\s*", "", l)
        if l:
            lines.append(l)
    return lines

def dedup_preserve_order(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\s+", " ", it).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out

def too_similar(a: str, b: str, thresh: float = 0.6) -> bool:
    ta = set(re.findall(r"[\uac00-\ud7a3A-Za-z0-9]+", (a or "").lower()))
    tb = set(re.findall(r"[\uac00-\ud7a3A-Za-z0-9]+", (b or "").lower()))
    if not ta or not tb:
        return False
    inter, union = len(ta & tb), len(ta | tb)
    return (inter / max(1, union)) >= thresh

def not_too_long(s: str, max_chars: int) -> str:
    s = s or ""
    return s if len(s) <= max_chars else s[:max_chars]

def first_sentence(s: str) -> str:
    """여러 줄/여러 문장일 때 첫 문장만."""
    s = safe_strip(s)
    s = s.splitlines()[0] if "\n" in s else s
    m = re.search(r"(.+?[\.\?!\uff1f])(\s|$)", s)
    return m.group(1).strip() if m else s

def ensure_question_mark(s: str) -> str:
    s = safe_strip(s)
    return s if s.endswith("?") or s.endswith("？") else (s + "?") if s else s
