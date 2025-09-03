# ares/api/services/metadata_service.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
import re

from ares.api.utils.common_utils import get_logger

_log = get_logger("meta")

__all__ = [
    "build_meta_from_inputs",
    "normalize_meta",
    "merge_metas",
    "validate_meta",
    "ncs_query_from_meta",
]

# ---------------------------------------------------------
# 설정
# ---------------------------------------------------------
@dataclass
class MetaConfig:
    # CSV 파싱 시 구분자들 (콤마/세미콜론/개행/탭)
    csv_separators: str = r"[,\n;\t]+"
    # 허용 키 집합(스키마 가드)
    allowed_keys = {
        "company", "division", "role", "location",
        "jd_kpis", "skills", "confidence", "source"
    }
    # 기본 confidence
    default_confidence: float = 1.0

CFG = MetaConfig()

# ---------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------
def _split_csv_like(s: str) -> List[str]:
    """
    콤마/세미콜론/개행/탭을 모두 구분자로 보고 스플릿.
    공백/중복 제거, 길이 1 이하 토큰 제거.
    """
    if not s:
        return []
    parts = re.split(CFG.csv_separators, s)
    out: List[str] = []
    seen = set()
    for p in parts:
        t = (p or "").strip()
        if not t:
            continue
        key = re.sub(r"\s+", " ", t).lower()
        if len(key) <= 0 or key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out

def _norm_str(x: Any) -> str:
    return re.sub(r"\s+", " ", (str(x or "")).strip())

def _dedup_list(xs: List[str]) -> List[str]:
    seen, out = set(), []
    for x in xs or []:
        k = re.sub(r"\s+", " ", (x or "").strip()).lower()
        if k and k not in seen:
            seen.add(k); out.append(x.strip())
    return out

# ---------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------
def build_meta_from_inputs(
    company: str,
    role: str,
    division: str = "",
    location: str = "",
    kpi_csv: str = "",
    skills_csv: str = ""
) -> Dict[str, Any]:
    """
    수동 입력값을 표준 메타 스키마로 변환.
    - company, role은 필수(빈 값이면 빈 메타 반환)
    - jd_kpis/skills는 CSV/세미콜론/개행 등 혼합 구분자 허용
    """
    company = _norm_str(company)
    role    = _norm_str(role)
    if not company or not role:
        _log.warning("수동 메타 입력: company/role 누락 → 빈 메타 반환")
        return {}

    meta: Dict[str, Any] = {
        "company": company,
        "division": _norm_str(division),
        "role": role,
        "location": _norm_str(location),
        "jd_kpis": _split_csv_like(kpi_csv),
        "skills": _split_csv_like(skills_csv),
        "confidence": CFG.default_confidence,  # 수동 입력 → 최고 신뢰
        "source": ["manual"],
    }
    return normalize_meta(meta)

def normalize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    메타 스키마/값 정규화:
    - 허용 키 외 제거
    - 문자열 정규화, 리스트 dedup
    - confidence 범위 [0.0, 1.0] 클램프
    - source는 중복 제거 후 리스트 유지
    """
    if not isinstance(meta, dict):
        return {}
    m: Dict[str, Any] = {}
    for k, v in meta.items():
        if k not in CFG.allowed_keys:
            continue
        if k in ("company", "division", "role", "location"):
            m[k] = _norm_str(v)
        elif k in ("jd_kpis", "skills"):
            if isinstance(v, str):
                m[k] = _split_csv_like(v)
            else:
                m[k] = _dedup_list([_norm_str(x) for x in (v or [])])
        elif k == "confidence":
            try:
                c = float(v)
            except Exception:
                c = CFG.default_confidence
            m[k] = max(0.0, min(1.0, c))
        elif k == "source":
            if isinstance(v, str):
                m[k] = _dedup_list([v])
            else:
                m[k] = _dedup_list([_norm_str(x) for x in (v or [])])

    # 기본키 보장
    m.setdefault("jd_kpis", [])
    m.setdefault("skills", [])
    m.setdefault("source", [])
    if "confidence" not in m:
        m["confidence"] = CFG.default_confidence
    return m

def merge_metas(primary: Dict[str, Any], *others: Dict[str, Any]) -> Dict[str, Any]:
    """
    여러 메타를 병합.
    - 문자열 필드(company/division/role/location): primary 우선, 없으면 차선 사용
    - 리스트 필드(jd_kpis/skills/source): 합집합 dedup
    - confidence: max 사용(가장 신뢰 높은 소스 기준)
    """
    merged = normalize_meta(primary or {})
    for m in others or []:
        mm = normalize_meta(m or {})
        # 문자열 우선 결합
        for k in ("company", "division", "role", "location"):
            if not merged.get(k) and mm.get(k):
                merged[k] = mm[k]
        # 리스트 합집합
        for k in ("jd_kpis", "skills", "source"):
            merged[k] = _dedup_list(list(merged.get(k, [])) + list(mm.get(k, [])))
        # confidence: 더 높은 값을 채택
        try:
            merged["confidence"] = max(float(merged.get("confidence", 0.0)), float(mm.get("confidence", 0.0)))
        except Exception:
            pass
    return merged

def validate_meta(meta: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    메타 유효성 검사.
    - 필수: company, role (빈 문자열이면 실패)
    - 길이/형식 간단 체크
    """
    m = normalize_meta(meta or {})
    errs: List[str] = []
    if not m.get("company"):
        errs.append("company 누락")
    if not m.get("role"):
        errs.append("role 누락")
    # 과도한 길이 방지(표시용 제약)
    for k in ("company", "division", "role", "location"):
        if len(m.get(k, "")) > 200:
            errs.append(f"{k} 길이 과다")
    if len(m.get("jd_kpis", [])) > 50:
        errs.append("jd_kpis 항목 과다(>50)")
    if len(m.get("skills", [])) > 100:
        errs.append("skills 항목 과다(>100)")
    return (len(errs) == 0, errs)

def ncs_query_from_meta(meta: Dict[str, Any]) -> str:
    """
    NCS 검색용 질의어 생성 규칙:
    role → division → company 순. (interview_service와 동일 정책)
    """
    m = normalize_meta(meta or {})
    for k in ("role", "division", "company"):
        if m.get(k):
            return m[k]
    return ""
