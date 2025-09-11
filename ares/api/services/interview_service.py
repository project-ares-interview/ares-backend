# ares/api/services/interview_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
import time
import json
import argparse
import sys

from ares.api.utils.ai_utils import chat
from ares.api.utils.common_utils import get_logger
from ares.api.config import INTERVIEW_CONFIG as CFG, PROMPTS
from ares.api.utils.text_utils import (
    safe_strip,
    normalize_lines,
    dedup_preserve_order,
    too_similar,
    not_too_long,
    first_sentence,
    ensure_question_mark,
)

# 🔎 NCS 하이브리드 검색/컨텍스트 주입 (선택)
try:
    from ares.api.utils import search_utils as ncs
except ImportError:
    ncs = None

_log = get_logger("interview")

__all__ = [
    "make_outline",
    "generate_main_question_ondemand",
    "generate_followups",
    "score_answer_starc",
    "AIGenerationError",
]


class AIGenerationError(Exception):
    """AI 모델 응답 생성에 최종적으로 실패했을 때 발생하는 예외"""
    pass

# =========================
# 내부 유틸
# =========================
def _safe_chat(
    msgs: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    fallback: str = "",
    retries: int = 2,
    backoff: float = 0.8
) -> str:
    last_err = None
    for i in range(retries + 1):
        try:
            out = chat(msgs, temperature=temperature, max_tokens=max_tokens)
            return out or fallback
        except Exception as e:
            last_err = e
            _log.warning(f"chat() 실패, 재시도 {i}/{retries}: {e}")
            time.sleep(backoff * (2 ** i))
    
    _log.error(f"chat() 최종 실패: {last_err}")
    # 최종 실패 시, fallback 대신 예외 발생
    raise AIGenerationError(f"AI 응답 생성에 최종 실패했습니다: {last_err}")

# =========================
# (선택) NCS 컨텍스트 주입
# =========================
def _resolve_ncs_query(ncs_query: Optional[str], meta: Optional[dict]) -> str:
    q = (ncs_query or "").strip()
    if not q and meta:
        q = (meta.get("role") or meta.get("division") or meta.get("company") or "").strip()
    return q

def _build_ncs_ctx(query: Optional[str], top: int, max_len: int) -> str:
    if not ncs or not query:
        return ""
    try:
        hits = ncs.search_ncs_hybrid(query, top=top)
        ctx = ncs.format_ncs_context(hits, max_len=max_len) or ""
        _log.info(f"NCS 컨텍스트: hits={len(hits)}, query='{query[:60]}'")
        return ctx
    except Exception as e:
        _log.warning(f"NCS 컨텍스트 생성 실패: {e}")
        return ""

# =========================
# 메타 주입
# =========================
def _inject_company_ctx(prompt: str, meta: dict | None) -> str:
    if not meta:
        return prompt
    
    def _s(x):
        return (x or "").strip()
    
    comp = _s(meta.get("company", ""))
    div  = _s(meta.get("division", ""))
    role = _s(meta.get("role", ""))
    loc  = _s(meta.get("location", ""))
    kpis = ", ".join([_s(x) for x in meta.get("jd_kpis",[]) if _s(x)])[:200]
    skills = ", ".join([_s(x) for x in meta.get("skills",[]) if _s(x)])[:200]
    
    ctx = (
        f"[회사 컨텍스트]\n"
        f"- 회사: {comp or '미상'} | 부서/직무: {div or '-'} / {role or '-'} | 근무지: {loc or '-'}\n"
        f"- KPI: {kpis or '-'} | 스킬: {skills or '-'}\n\n"
    )
    return ctx + prompt

# =========================
# USR 빌더 (+ NCS 컨텍스트)
# =========================
def _outline_usr(context: str, n: int, meta: dict | None, ncs_ctx: str) -> str:
    p = f"[컨텍스트]\n{not_too_long(context, CFG['CONTEXT_MAX_CHARS'])}\n\n"
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += (
        f"요구사항:\n- 섹션 {n}개\n- 한국어, 불릿 없음\n- 각 줄 8~24자, 명사형 위주\n"
        "출력: 섹션명만 줄바꿈으로 나열"
    )
    return _inject_company_ctx(p, meta)

def _main_usr(context: str, prev: List[str], difficulty: str, meta: dict | None, ncs_ctx: str) -> str:
    prev_block = "\n".join([f"- {q}" for q in (prev or [])]) or "- (없음)"
    p = f"[컨텍스트]\n{not_too_long(context, 8000)}\n\n"
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += (
        f"[이미 한 질문]\n{prev_block}\n\n"
        f"[난이도]\n{difficulty}\n\n"
        "출력: 메인 질문 한 문장만(70자 이내, 끝은 물음표). 중복/유사 금지."
    )
    return _inject_company_ctx(p, meta)

def _follow_usr(main_q: str, answer: str, k: int, meta: dict | None, ncs_ctx: str) -> str:
    p = (
        f"[메인 질문]\n{safe_strip(main_q)}\n\n"
        f"[지원자 답변]\n{not_too_long(safe_strip(answer), CFG['ANSWER_MAX_CHARS'])}\n\n"
    )
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += f"요구: 꼬리질문 {k}개, 서로 다른 카테고리에서 생성.\n출력: 줄바꿈으로 질문만 나열"
    return _inject_company_ctx(p, meta)

def _starc_usr(q: str, a: str, meta: dict | None, ncs_ctx: str) -> str:
    p = f"[질문]\n{safe_strip(q)}\n\n[답변]\n{safe_strip(a)}\n\n"
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += "출력: JSON만."
    return _inject_company_ctx(p, meta)

# =========================
# 1) 섹션 아웃라인
# =========================
def make_outline(context: str, n: int = 5, meta: dict | None = None, ncs_query: str | None = None) -> List[str]:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG['NCS_TOP_OUTLINE'], CFG['NCS_CTX_MAX_LEN'])
    
    msgs = [
        {"role": "system", "content": PROMPTS["SYS_OUTLINE"]},
        {"role": "user", "content": _outline_usr(context, n, meta, ncs_ctx)},
    ]

    try:
        out = _safe_chat(
            msgs,
            temperature=CFG['TEMPERATURE_OUTLINE'],
            max_tokens=CFG['MAX_TOKENS_OUTLINE'],
        )
        lines = dedup_preserve_order(normalize_lines(out))
        return lines[:n] if lines else ["문제해결", "협업", "품질", "리스크", "고객집착"][:n]
    except AIGenerationError:
        return ["문제해결", "협업", "품질", "리스크", "고객집착"][:n]

# =========================
# 2) 메인 질문 생성 (온디맨드 1개)
# =========================
def generate_main_question_ondemand(
    context: str,
    prev_questions: List[str],
    difficulty: str = "보통",
    meta: dict | None = None,
    ncs_query: str | None = None
) -> str:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG['NCS_TOP_MAIN'], CFG['NCS_CTX_MAX_LEN'])
    
    msgs = [
        {"role": "system", "content": PROMPTS["SYS_MAIN_Q"]},
        {"role": "user", "content": _main_usr(context, prev_questions, difficulty, meta, ncs_ctx)},
    ]

    fallback_q = "해당 직무 관련 핵심 경험을 한 가지 사례로 설명해 주시겠습니까?"
    try:
        out = _safe_chat(
            msgs,
            temperature=CFG['TEMPERATURE_MAIN'],
            max_tokens=CFG['MAX_TOKENS_MAIN'],
            fallback=fallback_q
        )
        q = first_sentence(out)
        if any(too_similar(q, pq) for pq in prev_questions or []):
            q = "이전 질문과 겹치지 않는 다른 핵심 경험을 한 가지 선택해 구체적으로 설명해 주시겠습니까?"
        return ensure_question_mark(q)
    except AIGenerationError:
        return fallback_q

# =========================
# 3) 꼬리질문 생성 (단순화 버전)
# =========================
def generate_followups(
    main_q: str,
    answer: str,
    k: int = 3,
    main_index: Optional[int] = None,
    meta: Optional[dict] = None,
    ncs_query: str | None = None,
) -> List[str]:
    k = min(k, CFG['MAX_FOLLOW_K'])
    if k <= 0: return []

    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG['NCS_TOP_FOLLOW'], CFG['NCS_CTX_MAX_LEN'])

    msgs = [
        {"role": "system", "content": PROMPTS["SYS_FOLLOW"]},
        {"role": "user", "content": _follow_usr(main_q, answer, k, meta, ncs_ctx)},
    ]

    fallback_fus = [
        "핵심 지표와 기준선/기간을 수치로 명확히 제시해 주시겠어요?",
        "본인 고유 의사결정과 선택 근거를 구체적으로 설명해 주시겠어요?",
        "주요 리스크와 대비 대안(플랜B/C)은 무엇이었나요?"
    ]
    try:
        out = _safe_chat(
            msgs,
            temperature=CFG['TEMPERATURE_FOLLOW'],
            max_tokens=CFG['MAX_TOKENS_FOLLOW'],
        )
        lines = dedup_preserve_order(normalize_lines(out))[:k]
        if not lines: lines = fallback_fus[:k]
    except AIGenerationError:
        lines = fallback_fus[:k]

    if main_index is not None:
        return [f"{main_index}-{i+1}. {q.strip()}" for i, q in enumerate(lines)]
    return lines

# =========================
# 4) STAR-C 평가 (가중합/등급 포함)
# =========================
def score_answer_starc(
    q: str,
    a: str,
    meta: dict | None = None,
    ncs_query: str | None = None
) -> Dict[str, Any]:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG['NCS_TOP_SCORE'], CFG['NCS_CTX_MAX_LEN'])
    
    msgs = [
        {"role": "system", "content": PROMPTS["SYS_STARC"]},
        {"role": "user", "content": _starc_usr(q, a, meta, ncs_ctx)},
    ]

    raw = ""
    try:
        raw = _safe_chat(
            msgs,
            temperature=CFG['TEMPERATURE_SCORE'],
            max_tokens=CFG['MAX_TOKENS_SCORE'],
        ).strip()
    except AIGenerationError as e:
        raw = f"평가 생성 실패: {e}"

    result: Dict[str, Any] = {
        "scores": {}, "weighted_total": None, "grade": None,
        "comments": {}, "summary": []
    }
    try:
        data = json.loads(raw)
        result.update(data)

        if result["scores"] and result.get("weighted_total") is None:
            s = float(result["scores"].get("S", 0))
            t = float(result["scores"].get("T", 0))
            a_score = float(result["scores"].get("A", 0))
            r = float(result["scores"].get("R", 0))
            c = float(result["scores"].get("C", 0))
            weighted = s*1.0 + t*1.0 + a_score*1.2 + r*1.2 + c*0.8
            result["weighted_total"] = round(weighted, 2)

        if result.get("grade") is None and result.get("weighted_total") is not None:
            wt = result["weighted_total"]
            if wt >= 22.5: grade = "A"
            elif wt >= 18.0: grade = "B"
            elif wt >= 13.0: grade = "C"
            else: grade = "D"
            result["grade"] = grade

    except (json.JSONDecodeError, TypeError) as e:
        _log.warning(f"STAR-C JSON 파싱 실패: {e} | raw={raw[:800]}")
        result["summary"] = [raw or "평가 생성 실패"]

    return result

# =========================
# CLI 테스트 진입점 (리팩토링 후)
# =========================
if __name__ == "__main__":
    # ... (CLI 로직은 필요 시 여기에 업데이트)
    pass
