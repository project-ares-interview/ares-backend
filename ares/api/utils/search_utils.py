# ares/api/utils/search_utils.py
from __future__ import annotations

from typing import List, Dict, Optional
import os
from ares.api.utils.common_utils import get_logger
_log = get_logger("search_ncs")

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

# 설정 및 공용 유틸리티 import
from ares.api.config import SEARCH_CONFIG, AI_CONFIG
from ares.api.utils.ai_utils import embed as _embed

# 최신 SDK에서 제공되는 모델 (없을 수도 있음)
try:
    from azure.search.documents.models import VectorizedQuery
except ImportError:  # 구버전 SDK
    VectorizedQuery = None

# =========================
# 환경 변수 (Credential 전용)
# =========================
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip()

# =========================
# 내부: SearchClient 생성
# =========================
def _client(index: Optional[str] = None) -> SearchClient:
    idx = (index or SEARCH_CONFIG["NCS_INDEX"]).strip()
    if not SEARCH_ENDPOINT or not SEARCH_KEY or not idx:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY / NCS_INDEX 환경변수 누락")
    return SearchClient(SEARCH_ENDPOINT, idx, AzureKeyCredential(SEARCH_KEY))

# =========================
# 내부: 스키마 호환 보정
# =========================
def _extract_fields(doc: Dict) -> Dict:
    # ... (이하 코드는 이전과 동일)
    out = {
        "doc_id": doc.get("doc_id") or doc.get("id") or "",
        "major_code":  doc.get("major_code"),
        "middle_code": doc.get("middle_code"),
        "minor_code":  doc.get("minor_code"),
        "detail_code": doc.get("detail_code"),
        "ability_code": doc.get("ability_code"),
        "ability_name": doc.get("ability_name"),
        "element_code": doc.get("element_code"),
        "element_name": doc.get("element_name"),
        "criteria_text": doc.get("criteria_text"),
        "content_concat": doc.get("content_concat"),
        "knowledge": doc.get("knowledge"),
        "skills": doc.get("skills"),
        "attitudes": doc.get("attitudes"),
    }
    if not (out["ability_name"] or out["element_name"] or out["major_code"]):
        cls = doc.get("classification") or {}
        au  = doc.get("ability_unit") or {}
        el  = doc.get("element") or {}
        out.update({
            "major_code":  (cls.get("major") or {}).get("code"),
            "middle_code": (cls.get("middle") or {}).get("code"),
            "minor_code":  (cls.get("minor") or {}).get("code"),
            "detail_code": (cls.get("detail") or {}).get("code"),
            "ability_code": au.get("code"),
            "ability_name": au.get("name"),
            "element_code": el.get("code"),
            "element_name": el.get("name"),
            "criteria_text": out["criteria_text"] or doc.get("criteria_text"),
            "content_concat": out["content_concat"] or doc.get("content_concat"),
        })
    return out

# =========================
# 내부: 벡터검색 시도
# =========================
def _try_vector_search(sc: SearchClient, args: Dict, vec: List[float], top: int):
    if VectorizedQuery is None:
        raise RuntimeError("VectorizedQuery 미제공 SDK 버전")

    try:
        vq = VectorizedQuery(
            vector=vec,
            k_nearest_neighbors=top,
            fields=SEARCH_CONFIG["NCS_VECTOR_FIELD"],
        )
        return sc.search(**args, vector_queries=[vq])
    except Exception as e:
        raise RuntimeError(f"vector_queries[VectorizedQuery] 실패: {e}")

# =========================
# 공개 API: 하이브리드 검색
# =========================
def search_ncs_hybrid(
    query_text: str,
    filters: Optional[str] = None,
    top: int = 8,
    select: Optional[List[str]] = None,
    index: Optional[str] = None,
) -> List[Dict]:
    sc = _client(index=index)
    vec = _embed(query_text)

    # 임베딩 차원 검사 (설정값에서 가져옴)
    if len(vec) != AI_CONFIG["EMBED_DIMENSIONS"]:
        raise ValueError(
            f'Embedding dimension mismatch: expected {AI_CONFIG["EMBED_DIMENSIONS"]}, got {len(vec)}'
        )

    select = select or [
        "doc_id",
        "major_code","middle_code","minor_code","detail_code",
        "ability_code","ability_name","ability_level",
        "element_code","element_name",
        "criteria_text","knowledge","skills","attitudes",
        "content_concat","source","updated_at",
    ]
    try:
        args = dict(search_text=query_text, top=top, filter=filters, select=select)

        if os.getenv("NCS_DISABLE_VECTOR", "").strip() == "1":
            _log.warning("[search_ncs_hybrid] NCS_DISABLE_VECTOR=1 → 키워드-only")
            results = sc.search(**args)
        else:
            try:
                results = _try_vector_search(sc, args, vec, top)
            except Exception as e:
                _log.warning("[search_ncs_hybrid] 벡터검색 실패 → 키워드-only 폴백: " + str(e))
                results = sc.search(**args)

        out: List[Dict] = []
        for r in results:
            item = dict(r)
            item.update(_extract_fields(item))
            score = getattr(r, "@search.score", None)
            item["_score"] = float(score) if score is not None else None
            item["source"] = "NCS"
            out.append(item)

        return out

    except HttpResponseError as e:
        _log.warning(f"[search_ncs_hybrid] Azure Search 오류: {e}")
        return []
    except Exception as e:
        _log.warning(f"[search_ncs_hybrid] 처리 실패: {e}")
        return []

# =========================
# 공개 API: 코드 기반 필터 + (선택) 질의
# =========================
def search_ncs_by_codes(
    # ... (이하 코드는 이전과 동일)
    major: Optional[str] = None,
    middle: Optional[str] = None,
    minor: Optional[str] = None,
    detail: Optional[str] = None,
    ability_code: Optional[str] = None,
    query_text: Optional[str] = "",
    top: int = 8,
    index: Optional[str] = None,
) -> List[Dict]:
    clauses = []
    if major:
        clauses.append(f"(major_code eq '{major}' or classification/major/code eq '{major}')")
    if middle:
        clauses.append(f"(middle_code eq '{middle}' or classification/middle/code eq '{middle}')")
    if minor:
        clauses.append(f"(minor_code eq '{minor}' or classification/minor/code eq '{minor}')")
    if detail:
        clauses.append(f"(detail_code eq '{detail}' or classification/detail/code eq '{detail}')")
    if ability_code:
        clauses.append(f"(ability_code eq '{ability_code}' or ability_unit/code eq '{ability_code}')")

    filt = " and ".join(clauses) if clauses else None
    q = (query_text or "").strip() or SEARCH_CONFIG["NCS_FALLBACK_QUERY"]
    return search_ncs_hybrid(query_text=q, filters=filt, top=top, index=index)

# =========================
# 공개 API: 프롬프트용 컨텍스트 문자열
# =========================
def format_ncs_context(hits: List[Dict], max_len: int = 2000) -> str:
    # ... (이하 코드는 이전과 동일)
    lines: List[str] = []
    for i, h in enumerate(hits, 1):
        ability = h.get("ability_name") or "-"
        element = h.get("element_name") or "-"
        citer = (h.get("criteria_text") or h.get("content_concat") or "").strip().replace("\n", " ")
        major_code = h.get("major_code") or ""
        minor_code = h.get("minor_code") or ""
        codes = "/".join([c for c in [major_code, minor_code, h.get("ability_code"), h.get("element_code")] if c])
        score = h.get("_blend_score", h.get("_score"))
        score_str = f" | score={score:.3f}" if isinstance(score, (int, float)) else ""
        line = f"[NCS#{i}] {codes or '-'} | 능력단위:{ability} | 요소:{element} | 기준:{citer[:400]}{score_str}"
        lines.append(line)
    txt = "\n".join(lines)
    return txt[:max_len]

# =========================
# 디버그/단독 실행
# =========================
if __name__ == "__main__":
    query = os.getenv("NCS_DEBUG_QUERY", "펌프 정비 절차")
    top = int(os.getenv("NCS_DEBUG_TOP", "5"))
    print(f"[debug] query='{query}' top={top}")
    hits = search_ncs_hybrid(query, top=top)
    print(f"[debug] got {len(hits)} hits")
    for i, h in enumerate(hits, 1):
        au = h.get("ability_name") or "-"
        el = h.get("element_name") or "-"
        citer = (h.get("criteria_text") or h.get("content_concat") or "")[:100].replace("\n", " ")
        print(f'{i:02d}. {h.get("doc_id")} | {au} / {el} | {citer}')
    ctx = format_ncs_context(hits, max_len=600)
    print("\n[NCS CONTEXT PREVIEW]\n" + ctx)
