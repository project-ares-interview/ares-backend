"""
Utility functions for the RAG Interview Bot.
"""

import json
import re
import unicodedata
from typing import Any, Dict, List

from ares.api.utils.ai_utils import safe_extract_json

def _escape_special_chars(text: str) -> str:
    pattern = r'([+\-&|!(){}\[\]^"~*?:\\])'
    return re.sub(pattern, r'\\\1', text or "")

def _natural_num(s: str) -> int:
    try:
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else 10**6
    except Exception:
        return 10**6

def _truncate(s: str, limit: int, tail: str = "…(truncated)") -> str:
    if not isinstance(s, str):
        s = str(s or "")
    return s if len(s) <= limit else (s[: max(0, limit - len(tail))] + tail)

def _extract_from_korean_schema(plan_data: Any) -> List[Dict]:
    """한글 스키마 -> 표준 스키마로 변환: list[{stage, objective?, questions:[...]}]"""
    if not isinstance(plan_data, (dict, list)):
        return []

    root = plan_data
    if isinstance(root, dict) and "면접 계획" in root and isinstance(root["면접 계획"], dict):
        stages_dict = root["면접 계획"]
    elif isinstance(root, dict) and any(k.endswith("단계") for k in root.keys()):
        stages_dict = root
    else:
        return []

    norm: List[Dict] = []
    for stage_key in sorted(stages_dict.keys(), key=_natural_num):
        stage_block = stages_dict.get(stage_key, {})
        if not isinstance(stage_block, dict):
            continue

        objective = (stage_block.get("목표") or stage_block.get("목 적") or "").strip() or None

        q_keys = ("질문", "핵심 질문", "문항", "questions")
        qs_raw = None
        for k in q_keys:
            if k in stage_block:
                qs_raw = stage_block.get(k)
                break
        if qs_raw is None:
            qs_raw = []

        qs_list: List[str] = []
        if isinstance(qs_raw, list):
            for item in qs_raw:
                if isinstance(item, str) and item.strip():
                    qs_list.append(item.strip())
                elif isinstance(item, dict):
                    q = (
                        item.get("질문")
                        or item.get("question")
                        or item.get("Q")
                        or item.get("텍스트")
                        or item.get("text")
                    )
                    if isinstance(q, str) and q.strip():
                        qs_list.append(q.strip())
        elif isinstance(qs_raw, dict):
            q = (
                qs_raw.get("질문")
                or qs_raw.get("question")
                or qs_raw.get("Q")
                or qs_raw.get("텍스트")
                or qs_raw.get("text")
            )
            if isinstance(q, str) and q.strip():
                qs_list.append(q.strip())

        fixed = []
        for q in qs_list:
            q = unicodedata.normalize("NFKC", q)
            if len(q) > 260:
                parts = re.split(r'(?<=[.!?])\s+', q)
                fixed.append(parts[0] if parts and parts[0] else q[:260])
            else:
                fixed.append(q)

        if fixed:
            norm.append({"stage": stage_key, "objective": objective, "questions": fixed})
    return norm

def _debug_print_raw_json(label: str, payload: str):
    try:
        head = payload[:800]
        tail = payload[-400:] if len(payload) > 1200 else ""
        print(f"\n--- {label} RAW JSON (len={len(payload)}) START ---\n{head}")
        if tail:
            print("\n... (snip) ...\n")
            print(tail)
        print(f"--- {label} RAW JSON END ---\n")
    except Exception:
        pass

def _force_json_like(raw: str) -> dict | list | None:
    """마크다운/설명문 섞인 응답에서 가장 바깥쪽 JSON 블록을 강제로 추출."""
    if not raw:
        return None
    raw2 = re.sub(r'^```(?:json)?|```', "", raw.strip(), flags=re.MULTILINE)
    for open_ch, close_ch in [("{ ", " }"), ("[", "]")]:
        start = raw2.find(open_ch)
        end = raw2.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            candidate = raw2[start : end + 1]
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None

def _normalize_plan_local(plan_data: Any) -> List[Dict]:
    """다양한 변형 스키마를 표준 list[{stage, objective?, questions:[...]}] 로 정규화."""
    if not plan_data:
        return []

    if isinstance(plan_data, str):
        plan_data = safe_extract_json(plan_data, default=None) or _force_json_like(plan_data) or {}

    # 1) 한국어 스키마
    ko_norm = _extract_from_korean_schema(plan_data)
    if ko_norm:
        return ko_norm

    # 2) 일반/영문
    candidate = (
        plan_data.get("plan")
        if isinstance(plan_data, dict) and "plan" in plan_data
        else plan_data.get("interview_plan")
        if isinstance(plan_data, dict) and "interview_plan" in plan_data
        else plan_data
    )

    if isinstance(candidate, dict):
        if "stage" in candidate and any(k in candidate for k in ("questions", "question", "items")):
            candidate = [candidate]
        else:
            candidate = [v for v in candidate.values() if isinstance(v, dict)]

    if not isinstance(candidate, list):
        return []

    norm: List[Dict] = []
    for i, st in enumerate(candidate, 1):
        if not isinstance(st, dict):
            continue
        stage = st.get("stage") or f"Stage {i}"
        objective = st.get("objective") or st.get("goal") or st.get("purpose") or st.get("objectives")
        qs = st.get("questions") or st.get("question") or st.get("items") or []
        if isinstance(qs, str):
            qs = [qs]
        qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]

        fixed_qs = []
        for q in qs:
            if len(q) > 260:
                m = re.split(r'(?<=[.!?])\s+', q)
                fixed_qs.append(m[0] if m and m[0] else q[:260])
            else:
                fixed_qs.append(q)

        if fixed_qs:
            norm.append({"stage": stage, "objective": objective, "questions": fixed_qs})
    return norm

def _chunked(iterable, size):
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf

def ensure_min_questions(plan_list: List[Dict], min_per_stage: int = 1) -> List[Dict]:
    fixed = []
    for st in plan_list:
        if not isinstance(st, dict):
            continue
        title = st.get("stage") or "Untitled Stage"
        qs = [q for q in (st.get("questions") or []) if isinstance(q, str) and q.strip()]
        if not qs:
            qs = ["해당 단계의 핵심 역량을 드러낼 수 있는 최근 사례를 STAR로 설명해 주세요."]
        fixed.append({"stage": title, "objective": st.get("objective"), "questions": qs[:max(1, min_per_stage)]})
    return fixed
