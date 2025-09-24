# ares/api/services/rag/bot/utils.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 타입 별칭
Plan = Dict[str, Any]

__all__ = [
    # JSON 유틸
    "_looks_like_json",
    "_force_json_like",
    "normalize_llm_json",
    "safe_get_any",
    # 문자열/로깅/리스트 유틸
    "_truncate",
    "_escape_special_chars",
    "_debug_print_raw_json",
    "_chunked",
    # 인터뷰 플랜 스키마 유틸
    "normalize_interview_plan",
    "extract_first_main_question",
]

# ============================================================================
# JSON Normalization / Parsing
# ============================================================================

def _looks_like_json(s: str) -> bool:
    """
    문자열이 JSON 객체/배열처럼 보이는지 단순 휴리스틱 체크.
    (이전 버그: 엉뚱한 리터럴과 비교하던 부분을 { / [ 로 복구)
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    return (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]"))

def _strip_code_fence(raw: str) -> str:
    """
    ```json ... ``` 혹은 ``` ... ``` 코드펜스를 제거
    """
    if not isinstance(raw, str):
        return ""
    txt = raw.strip()
    # ```json ... ```
    txt = re.sub(r"^```(?:json)?\s*|```$", "", txt, flags=re.IGNORECASE | re.MULTILINE)
    return txt.strip()

def _force_json_like(raw: str) -> dict | list | None:
    """
    LLM이 코드펜스/잡다한 접두/접미를 붙여도 JSON 코어를 최대한 추출해 파싱 시도.
    - 코드펜스 제거
    - 문자열에서 가장 바깥쪽 {...} 또는 [...] 구간을 잘라 파싱
    """
    if not raw:
        return None
    raw2 = _strip_code_fence(raw)

    # 1) 전체가 이미 JSON처럼 보이면 바로 시도
    if _looks_like_json(raw2):
        try:
            return json.loads(raw2)
        except Exception:
            pass

    # 2) 포함된 첫 {..} 혹은 [..] 블록을 찾아 파싱 시도
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = raw2.find(open_ch)
        end = raw2.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            candidate = raw2[start : end + 1]
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None

def _json_parse_maybe(s: Any) -> Any:
    """
    값이 문자열이면 JSON 코드펜스 제거 및 파싱을 시도한다.
    실패하면 원본을 그대로 돌려준다.
    """
    if isinstance(s, str):
        txt = _strip_code_fence(s)
        if _looks_like_json(txt):
            try:
                return json.loads(txt)
            except Exception:
                return s
    return s

def _clean_key(k: Any) -> str:
    """dict 키를 느슨하게 정규화 (따옴표/공백 제거)"""
    return str(k).strip().strip("'").strip('"')

def _normalize_tree(obj: Any, depth: int = 0, max_depth: int = 5) -> Any:
    """
    트리 전체를 내려가며 문자열 JSON을 실제 객체로 바꾸고
    dict 키를 느슨하게 정규화한다.
    """
    if depth > max_depth:
        return obj
    obj = _json_parse_maybe(obj)
    if isinstance(obj, dict):
        return { _clean_key(k): _normalize_tree(v, depth + 1, max_depth) for k, v in obj.items() }
    if isinstance(obj, list):
        return [ _normalize_tree(v, depth + 1, max_depth) for v in obj ]
    return obj

def normalize_llm_json(payload: Any) -> Any:
    """
    LLM 응답에 흔한 변형(코드펜스/키 들쭉날쭉/중첩 문자열 JSON)을 정규화.
    Planner/Analyzer 등에서 1차 정돈용으로 사용.
    """
    return _normalize_tree(payload, 0, 5)

def safe_get_any(d: dict, *candidates: str):
    """
    dict에서 후보 키들 중 존재하는 첫 값을 반환.
    - 키 문자열 변형(따옴표/공백)도 느슨하게 허용.
    """
    if not isinstance(d, dict):
        return None
    keys = set()
    for c in candidates:
        if not isinstance(c, str):
            continue
        keys.update({
            c,
            _clean_key(c),
            c.strip(),
            c.strip().strip("'").strip('"'),
        })
    for k in list(d.keys()):
        ck = _clean_key(k)
        if ck in keys or k in keys:
            return d[k]
    return None

# ============================================================================
# String / Logging / Iteration Utilities
# ============================================================================

def _truncate(s: Any, limit: int, tail: str = "…(truncated)") -> str:
    """긴 문자열 잘라내기 (None/비문자열도 안전 처리)"""
    if not isinstance(s, str):
        s = str(s or "")
    return s if len(s) <= limit else (s[: max(0, limit - len(tail))] + tail)

def _escape_special_chars(text: str) -> str:
    """
    검색/질의에 쓰일 수 있는 특수문자를 이스케이프.
    (루씬/검색엔진 특수문자 기준)
    """
    pattern = r'([+\-&|!(){}\[\]^"~*?:\\])'
    return re.sub(pattern, r'\\\1', text or "")

def _debug_print_raw_json(label: str, payload: str):
    """
    개발 중 디버깅용(콘솔 출력). 안전 실패 무시.
    Django settings 의존 제거(단순 print) — 필요 시 로거로 교체 가능.
    """
    try:
        txt = payload or ""
        head = txt[:800]
        tail = txt[-400:] if len(txt) > 1200 else ""
        print(f"\n--- {label} RAW JSON (len={len(txt)}) START ---\n{head}")
        if tail:
            print("\n... (snip) ...\n")
            print(tail)
        print(f"--- {label} RAW JSON END ---\n")
    except Exception:
        pass

def _chunked(iterable, size: int):
    """리스트를 size 단위로 청크 분할"""
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf

# ============================================================================
# Interview Plan Schema Normalization
# ============================================================================

def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

def normalize_interview_plan(plan: dict) -> dict:
    """
    다양한 스키마 변형을 표준 스키마로 정규화.
    V2 호환: icebreaking 질문을 찾아 최상위 키로 분리.
    표준 스키마:
    {
      "icebreakers": [{"id": "...", "text": "..."}],
      "stages": [
        {
          "title": "...",
          "questions": [
            {"id": "1-1", "text": "...", "followups": [{"text":"..."}]}
          ]
        }
      ]
    }
    """
    if not isinstance(plan, dict):
        return {"icebreakers": [], "stages": []}

    # 다양한 루트 키 가능성 처리
    root = plan
    if "interview_plan" in plan:
        root = {"stages": plan["interview_plan"]}
    elif "raw_v2_plan" in plan:
        root = plan["raw_v2_plan"]

    ice = root.get("icebreakers") or []
    stages = root.get("stages") or root.get("plan") or root.get("phases") or []
    if isinstance(stages, dict):
        stages = [stages]

    norm_stages = []
    norm_ice = []

    # 1. 아이스브레이킹 질문을 먼저 찾아 분리
    ice_list = _as_list(ice)
    for ib in ice_list:
        if isinstance(ib, str):
            t = ib.strip()
            i = None
        elif isinstance(ib, dict):
            t = (ib.get("text") or ib.get("question") or "").strip()
            i = ib.get("id")
        else:
            t = ""
            i = None
        if t:
            norm_ice.append({"id": i, "text": t})

    # 2. 메인 스테이지 처리
    for si, s in enumerate(stages, 1):
        if not isinstance(s, dict):
            qtxt = str(s).strip()
            if qtxt:
                norm_stages.append({
                    "title": f"Stage {si}",
                    "questions":[{"id":f"{si}-1","text":qtxt,"followups":[]}]
                })
            continue

        title = s.get("title") or s.get("name") or s.get("stage") or s.get("phase") or f"Stage {si}"
        qs = s.get("questions") or s.get("items") or []
        if isinstance(qs, dict):
            qs = [qs]

        norm_qs = []
        temp_qs = [] # 아이스브레이커가 아닌 질문만 임시 저장

        for qi, q in enumerate(qs, 1):
            q_dict = None
            if isinstance(q, str):
                txt = q.strip()
                if txt:
                    q_dict = {"id": f"{si}-{qi}", "text": txt, "followups": [], "question_type": "unknown"}
            elif isinstance(q, dict):
                question_content = q.get("text") or q.get("question") or q.get("q")
                if isinstance(question_content, dict):
                    txt = (question_content.get("text") or "").strip()
                else:
                    txt = str(question_content or "").strip()
                
                q_type = q.get("question_type", "unknown")
                q_id = q.get("id", f"{si}-{qi}")
                fus = q.get("followups") or q.get("followup") or []
                if isinstance(fus, dict):
                    fus = [fus]
                fus_norm = []
                for fu in fus:
                    if isinstance(fu, str):
                        ftxt = fu.strip()
                    elif isinstance(fu, dict):
                        ftxt = (fu.get("text") or fu.get("question") or "").strip()
                    else:
                        ftxt = ""
                    if ftxt:
                        fus_norm.append({"text": ftxt})
                if txt:
                    q_dict = {"id": q_id, "text": txt, "followups": fus_norm, "question_type": q_type}
            
            if q_dict:
                # 아이스브레이킹 질문이면 norm_ice로 이동, 아니면 temp_qs에 추가
                if q_dict.get("question_type") == "icebreaking":
                    norm_ice.append(q_dict)
                else:
                    temp_qs.append(q_dict)
        
        norm_qs.extend(temp_qs)
        if norm_qs:
             norm_stages.append({"title": str(title), "questions": norm_qs})

    return {"icebreakers": norm_ice, "stages": norm_stages}

def extract_first_main_question(plan: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    표준 스키마(plan)에서 첫 메인 질문을 찾아 (text, id) 반환.
    없으면 (None, None).
    """
    if not isinstance(plan, dict):
        return None, None
    for s in plan.get("stages", []):
        for q in s.get("questions", []):
            txt = (q.get("text") or "").strip()
            if txt:
                return txt, q.get("id")
    return None, None


def _extract_numbers(text: str) -> List[str]:
    # 12, 12%, 1.5배, 2배, 3개월 등 폭넓게 커버
    return re.findall(r"\d+(?:\.\d+)?\s*(?:%|배|개월|주|일|시간)?", text)

def normalize_after_sanitization(q: str) -> str:
    """
    '효율을 구체적인 수치 향상시킨' 같은 어색한 구문을 자연스런 물음으로 바꿔줌
    """
    q = re.sub(r"(구체적인 수치)\s*향상시킨", "얼마나 개선되었는지", q)
    q = re.sub(r"(구체적인 수치)\s*감소시킨", "얼마나 감소시켰는지", q)
    q = re.sub(r"(구체적인 수치)\s*단축한", "얼마나 단축했는지", q)
    # 필요 시 패턴 추가
    return q

def sanitize_question_against_resume(question: str, resume_blob: str) -> str:
    """
    질문 내 정량표현이 이력서/자소서 원문에 존재하지 않으면 일반화 표현으로 치환.
    """
    original_question = question
    for token in set(_extract_numbers(question)):
        if token not in resume_blob:
            # 숫자 토큰이 원문에 없으면 일반화
            question = question.replace(token, "구체적인 수치")
    
    # 문장 패턴 보정
    question = normalize_after_sanitization(question)
    
    if original_question != question:
        logger.info("[PLAN-SANITIZED] before=%s || after=%s", original_question, question)
        
    return question

def sanitize_plan_questions(plan: Dict, resume_blob: str) -> Dict:
    """
    phases[*].items[*].question / followups[*] 전체에 Sanitizer 적용
    """
    if not isinstance(plan, dict):
        return plan
    phases = plan.get("phases") or []
    for ph in phases:
        items = ph.get("items") or []
        for it in items:
            q = it.get("question")
            if isinstance(q, str):
                it["question"] = sanitize_question_against_resume(q, resume_blob)
            fus = it.get("followups") or []
            new_fus = []
            for fu in fus:
                if isinstance(fu, str):
                    new_fus.append(sanitize_question_against_resume(fu, resume_blob))
            it["followups"] = new_fus
    return plan
