# ares/api/utils/text_utils.py
"""
텍스트 전처리/보정 유틸리티 모음

주요 기능
- 안전한 strip, 라인 정규화, 중복 제거, 유사도 판단
- 첫 문장 추출, 물음표 보장
- 🔧 정제 결과가 중간에 끊긴 경우 RAW 원문으로 자연스럽게 꼬리를 이어붙이는 ensure_full_text()

사용 예:
    safe = ensure_full_text(refined_resume_context, raw_resume_context)
"""

from __future__ import annotations
import re
from typing import List, Optional


# ---------------------------
# 기본 전처리 유틸
# ---------------------------
def safe_strip(s: str) -> str:
    return (s or "").strip()


def normalize_lines(text: str) -> List[str]:
    """
    - 공백 라인 제거
    - 불릿/번호 접두 제거: -, •, 숫자., ), ] 등
    """
    lines: List[str] = []
    for raw in (text or "").splitlines():
        l = raw.strip()
        if not l:
            continue
        l = re.sub(r"^[\-•\d\.\)\(\]]+\s*", "", l)
        if l:
            lines.append(l)
    return lines


def dedup_preserve_order(items: List[str]) -> List[str]:
    """
    공백/대소문자/연속 공백 무시하고 순서 유지 중복 제거
    """
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\s+", " ", it).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def too_similar(a: str, b: str, thresh: float = 0.6) -> bool:
    """
    토큰 Jaccard 유사도(아주 러프)로 유사성 판단
    """
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


# ---------------------------
# 끊김/미완 보정 보조 유틸
# ---------------------------
_SENT_END_RE = re.compile(r"[\.!?。\?!]\s*$")
_KO_CONNECTIVES_RE = re.compile(
    r"(으로|로|고|며|지만|그리고|또한|때문에|위해|하며|하면서|인데|인데도|이지만|인데요|거든요|이라고)$"
)
_CODE_FENCE = "```"


def _balance_code_fences(s: str) -> str:
    """
    백틱 코드펜스 개수가 홀수면 닫아줌.
    """
    if not s:
        return s
    fences = s.count(_CODE_FENCE)
    if fences % 2 == 1:
        s += "\n" + _CODE_FENCE
    return s


def _close_open_lists(s: str) -> str:
    """
    마크다운 리스트가 어색하게 끝나는 경우 개행 하나 추가 정도로 완충.
    """
    return (s or "").rstrip() + "\n"


def _looks_truncated(s: str) -> bool:
    """
    - 문장 종료부호 없이 연결어/조사로 끝나면 끊긴 걸로 간주
    - 코드펜스가 열리고 닫히지 않은 경우도 끊김으로 간주
    """
    if not s:
        return False
    tail = s.strip()[-40:]
    if s.count(_CODE_FENCE) % 2 == 1:
        return True
    if not _SENT_END_RE.search(tail) and _KO_CONNECTIVES_RE.search(tail):
        return True
    # 괄호/따옴표 대충 균형 체크(완벽X)
    opens = s.count("(") + s.count("[") + s.count("{") + s.count("“") + s.count('"')
    closes = s.count(")") + s.count("]") + s.count("}") + s.count("”") + s.count('"')
    return opens > closes


def _ensure_sentence_end(s: str) -> str:
    """
    끝이 너무 허전하면 마침표 하나 추가 (코드펜스/헤더 제외)
    """
    t = s.rstrip()
    if not t:
        return t
    if t.endswith(_CODE_FENCE):
        return t
    if re.search(r"(#|\*|-|\d+\.)\s*$", t):
        return t
    if not _SENT_END_RE.search(t[-8:]):
        return t + "."
    return t


def _find_anchor_in_raw(refined: str, raw: str, window: int) -> int:
    """
    refined 끝부분(window)을 anchor로 하여 raw에서 마지막 등장 위치를 찾음.
    없으면 -1 반환.
    """
    if not refined or not raw:
        return -1
    anchor = refined[-window:].splitlines()[-1].strip()
    if not anchor:
        return -1
    return raw.rfind(anchor)


def _truncate_at_sentence_boundary(s: str, max_len: int) -> str:
    """
    tail을 너무 길게 붙이지 않도록 문장 경계 또는 길이 제한에서 자른다.
    """
    s = s[: max_len]
    # 가장 마지막 문장부호 위치까지 자르기
    m = list(re.finditer(r"[\.!?。\?!]", s))
    if m:
        cut = m[-1].end()
        return s[:cut]
    return s


# ---------------------------
# 공개: 끊김 보정 + RAW 머지
# ---------------------------
def ensure_full_text(
    refined: str,
    raw: str,
    *,
    tail_window: int = 400,
    tail_limit: int = 2000
) -> str:
    """
    정제 텍스트(refined)가 미완성으로 끝나면 RAW에서 자연스럽게 꼬리를 이어붙인다.

    동작:
    1) 기본 정리: 리스트/코드펜스 균형 보정
    2) 끊김 감지(_looks_truncated)
    3) 끊김 시 RAW에서 refined의 말미(anchor)를 찾아 그 이후 tail을 최대 tail_limit까지 붙임
       - tail은 문장 경계 우선으로 잘라서 붙임
    4) 마지막 마무리(마침표/코드펜스 균형) 보정

    반환: 보정된 텍스트
    """
    refined = refined or ""
    raw = raw or ""

    out = _close_open_lists(refined)
    out = _balance_code_fences(out)

    if _looks_truncated(out) and len(raw) > len(out):
        pos = _find_anchor_in_raw(out, raw, tail_window)
        if pos != -1:
            tail = raw[pos + len(out[-tail_window:].splitlines()[-1].strip()):]
            tail = _truncate_at_sentence_boundary(tail.strip(), tail_limit)
            if tail:
                # 공백 조정 후 붙임
                sep = "" if out.endswith("\n") or tail.startswith("\n") else " "
                out = out + sep + tail

    out = _ensure_sentence_end(out)
    out = _balance_code_fences(out)
    return out


# 모듈 외부에서 쉽게 찾도록 export 목록에 명시 (선택)
__all__ = [
    "safe_strip",
    "normalize_lines",
    "dedup_preserve_order",
    "too_similar",
    "not_too_long",
    "first_sentence",
    "ensure_question_mark",
    "ensure_full_text",
]
