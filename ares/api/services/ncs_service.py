# ares/api/services/ncs_service.py
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import re, json, argparse, sys

# 의존성: ares.api.utils.search_utils (선택)
try:
    from ares.api.utils.search_utils import search_ncs_hybrid  # type: ignore
except Exception:
    search_ncs_hybrid = None  # type: ignore

# 메타 스키마 헬퍼(선택)
try:
    from ares.api.services.metadata_service import ncs_query_from_meta  # type: ignore
except Exception:
    ncs_query_from_meta = None  # type: ignore

from ares.api.utils.common_utils import get_logger

_log = get_logger("ncs")

__all__ = [
    "summarize_top_ncs",
    "summarize_top_ncs_with_meta",
    "format_ncs_summary_md",
    "format_ncs_context",
    "search_top_raw",
]

# ---------------------------------------------------------
# 설정
# ---------------------------------------------------------
@dataclass
class NCSConfig:
    max_query_len: int = 2000
    max_top_cap: int = 20
    max_elements_per_ability: int = 5
    max_samples_per_ability: int = 3
    max_criteria_len: int = 200
    debug_log_queries: bool = False

CFG = NCSConfig()

# ---------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n]

def _safe_top(top: int) -> int:
    try:
        t = int(top)
    except Exception:
        t = 8
    return max(1, min(CFG.max_top_cap, t))

def _dedup_list(xs: List[str]) -> List[str]:
    seen, out = set(), []
    for x in xs or []:
        k = _norm(x).lower()
        if k and k not in seen:
            seen.add(k); out.append(x.strip())
    return out

# ---------------------------------------------------------
# 원시 검색 래퍼
# ---------------------------------------------------------
def search_top_raw(query: str, top: int = 8) -> List[Dict[str, Any]]:
    """
    NCS 하이브리드 검색의 안전 래퍼.
    - search_utils 미설치/실패 시 빈 리스트 반환
    - 로그/입력 길이 가드
    """
    q = _truncate(_norm(query), CFG.max_query_len)
    t = _safe_top(top)
    if CFG.debug_log_queries:
        _log.debug(f"[NCS] query='{q[:120]}', top={t}")
    if not q:
        return []
    if not callable(search_ncs_hybrid):
        _log.warning("search_ncs_hybrid 가용하지 않음")
        return []
    try:
        hits = search_ncs_hybrid(q, top=t)  # type: ignore[misc]
        return hits or []
    except Exception as e:
        _log.warning(f"NCS 검색 실패: {e}")
        return []

# ---------------------------------------------------------
# 핵심: 상위 NCS 요약
# ---------------------------------------------------------
def summarize_top_ncs(job_title: str, jd_text: str, top: int = 8) -> List[Dict[str, Any]]:
    """
    입력된 직무명/JP 텍스트를 결합해 NCS 상위 역량을 요약.
    반환: [ {ability_code, ability_name, elements[], criteria_samples[]}, ... ]
    """
    title = _norm(job_title)
    jd = _norm(jd_text)
    query = (title + "\n" + jd).strip()
    hits = search_top_raw(query, top=_safe_top(top))

    agg: Dict[str, Dict[str, Any]] = {}
    for h in hits:
        if not isinstance(h, dict):
            continue
        code = (h.get("ability_code") or h.get("doc_id") or "").strip()
        if not code:
            # 식별키가 없으면 스킵
            _log.debug("hit without ability_code/doc_id skipped")
            continue
        name = _norm(h.get("ability_name", ""))
        elem = _norm(h.get("element_name", ""))
        crit = _norm(h.get("criteria_text", ""))

        rec = agg.setdefault(code, {
            "ability_code": code,
            "ability_name": name,
            "elements": [],
            "criteria_samples": [],
        })
        if name and not rec.get("ability_name"):
            rec["ability_name"] = name
        if elem:
            rec["elements"].append(elem)
        if crit:
            rec["criteria_samples"].append(_truncate(crit, CFG.max_criteria_len))

    out: List[Dict[str, Any]] = []
    for code, rec in agg.items():
        rec["elements"] = _dedup_list(rec.get("elements", []))[:CFG.max_elements_per_ability]
        rec["criteria_samples"] = rec.get("criteria_samples", [])[:CFG.max_samples_per_ability]
        out.append({
            "ability_code": rec.get("ability_code", code),
            "ability_name": rec.get("ability_name", ""),
            "elements": rec["elements"],
            "criteria_samples": rec["criteria_samples"],
        })
    return out[:_safe_top(top)]

# 메타 기반 질의: role → division → company 우선
def summarize_top_ncs_with_meta(meta: Optional[Dict[str, Any]], jd_text: str, top: int = 8) -> List[Dict[str, Any]]:
    """
    metadata_service의 메타(dict)를 받아 NCS 쿼리를 자동 생성한 뒤 요약.
    role → division → company 우선 규칙을 따름.
    """
    job_title = ""
    try:
        if callable(ncs_query_from_meta):
            job_title = ncs_query_from_meta(meta or {})  # role→division→company
    except Exception:
        pass
    return summarize_top_ncs(job_title, jd_text, top=top)

# ---------------------------------------------------------
# 표현 유틸
# ---------------------------------------------------------
def format_ncs_summary_md(summary: List[Dict[str, Any]]) -> str:
    """
    summarize_top_ncs(...) 결과를 마크다운 불릿으로 요약.
    """
    if not summary:
        return "_NCS 요약 결과가 없습니다._"
    lines: List[str] = []
    for i, rec in enumerate(summary, 1):
        name = rec.get("ability_name") or rec.get("ability_code", f"Ability {i}")
        elems = ", ".join(rec.get("elements", []))
        samps = "; ".join(rec.get("criteria_samples", []))
        lines.append(f"- **{name}** · 요소: {elems if elems else '-'}")
        if samps:
            lines.append(f"  - 예시: {samps}")
    return "\n".join(lines)

def format_ncs_context(hits: List[Dict[str, Any]], max_len: int = 1800) -> str:
    """
    LLM 프롬프트에 주입 가능한 '컨텍스트 문자열' 생성.
    - interview_service의 NCS 컨텍스트 주입과 동일한 용도/스타일로 사용 가능.
    """
    if not hits:
        return ""
    # hits를 summarize 형태로 변환 후 압축
    # (요약을 바로 받은 경우에도 필드명이 동일하므로 그대로 처리)
    def _one(rec: Dict[str, Any]) -> str:
        name = rec.get("ability_name") or rec.get("ability_code", "")
        elems = ", ".join(rec.get("elements", [])) if rec.get("elements") else ""
        samps = "; ".join(rec.get("criteria_samples", [])) if rec.get("criteria_samples") else ""
        parts = [f"[{name}]"]
        if elems: parts.append(f"요소: {elems}")
        if samps: parts.append(f"예시: {samps}")
        return " ".join([p for p in parts if p]).strip()

    lines = [_one(h) for h in hits if isinstance(h, dict)]
    ctx = "\n".join([ln for ln in lines if ln]).strip()
    return _truncate(ctx, max_len)

# ---------------------------------------------------------
# CLI: 빠른 점검
# ---------------------------------------------------------
def _cli():
    p = argparse.ArgumentParser(description="NCS service quick test")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_sum = sub.add_parser("sum")
    p_sum.add_argument("--title", default="")
    p_sum.add_argument("--jd", default="")
    p_sum.add_argument("--top", type=int, default=8)

    p_meta = sub.add_parser("meta")
    p_meta.add_argument("--meta_json", required=True)
    p_meta.add_argument("--jd", default="")
    p_meta.add_argument("--top", type=int, default=8)

    p_fmt = sub.add_parser("fmt")
    p_fmt.add_argument("--title", default="")
    p_fmt.add_argument("--jd", default="")
    p_fmt.add_argument("--top", type=int, default=8)

    args = p.parse_args()

    if args.cmd == "sum":
        s = summarize_top_ncs(args.title, args.jd, top=args.top)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.cmd == "meta":
        meta = {}
        try:
            meta = json.loads(args.meta_json)
        except Exception as e:
            _log.error(f"메타 JSON 파싱 실패: {e}")
        s = summarize_top_ncs_with_meta(meta, args.jd, top=args.top)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.cmd == "fmt":
        s = summarize_top_ncs(args.title, args.jd, top=args.top)
        print(format_ncs_summary_md(s))
        print("\n---\n[컨텍스트]\n")
        print(format_ncs_context(s, max_len=1800))

if __name__ == "__main__":
    try:
        _cli()
    except Exception as e:
        _log.error(f"ncs_service CLI 실패: {e}")
        sys.exit(1)
