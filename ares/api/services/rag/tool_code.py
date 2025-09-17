# ares/api/services/rag/tool_code.py
"""
웹 검색 유틸 (DuckDuckGo via `ddgs`)
- API Key 불필요
- 경량/저비용 RAG 보조 용도
"""

from __future__ import annotations

from typing import List, Dict, Optional

# pip: ddgs
from ddgs import DDGS


def _truncate(s: str, limit: int = 500) -> str:
    """긴 본문을 콘솔/로그 친화적으로 축약."""
    if not isinstance(s, str):
        s = str(s or "")
    if len(s) <= limit:
        return s
    return s[: limit - 10] + "...(truncated)"


class GoogleSearch:
    """
    DuckDuckGo(DDGS)로 간단 웹 검색 수행.
    - 문자열 합본 반환: search()
    - 원시 리스트 반환: search_raw()
    """

    def __init__(self):
        # ddgs는 이 방식으로 인스턴스화해서 재사용 가능
        self.client = DDGS()

    def search_raw(
        self,
        queries: List[str],
        num_results: int = 3,
        *,
        region: str = "kr-kr",
        safesearch: str = "moderate",  # "off" | "moderate" | "strict"
        timelimit: Optional[str] = None,  # 예: "d", "w", "m", "y"
    ) -> List[Dict]:
        """
        검색 결과를 원시 리스트로 반환.
        각 아이템 예시: {"title": "...", "href": "...", "body": "...", "date": "...", "source": "..."}
        """
        out: List[Dict] = []
        if not queries:
            return out

        for q in queries:
            try:
                # ddgs.text(query, ...) — positional 인자로 쿼리 전달
                hits = list(
                    self.client.text(
                        q,
                        max_results=max(1, int(num_results)),
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                    )
                )
                # ddgs는 제너레이터를 반환하므로 list로 고정
                # 각 hit는 dict 형태 (title, href, body, date, source 등)
                for h in hits:
                    # 키 방어 (없을 수 있음)
                    out.append(
                        {
                            "query": q,
                            "title": h.get("title", ""),
                            "href": h.get("href", ""),
                            "body": h.get("body", ""),
                            "date": h.get("date", ""),
                            "source": h.get("source", ""),
                        }
                    )
            except Exception as e:
                out.append({"query": q, "error": str(e)})
        return out

    def search(
        self,
        queries: List[str],
        num_results: int = 3,
        *,
        region: str = "kr-kr",
        safesearch: str = "moderate",
        timelimit: Optional[str] = None,
    ) -> str:
        """
        검색 결과를 사람이 읽기 좋은 **문자열**로 합쳐 반환.
        RAG 보조 컨텍스트로 바로 주입하기 편리함.
        """
        results = self.search_raw(
            queries,
            num_results=num_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
        )

        if not results:
            return "검색 요청이 비어 있거나 결과가 없습니다.\n"

        lines: List[str] = []
        # 쿼리별 그룹핑(간단)
        current_q = None
        rank = 0

        for item in results:
            q = item.get("query", "")
            if q != current_q:
                current_q = q
                rank = 0
                lines.append(f"'{q}'에 대한 검색 결과:")
            if "error" in item:
                lines.append(f"  ! 오류: {item['error']}\n")
                continue

            rank += 1
            title = item.get("title", "")
            body = _truncate(item.get("body", ""), 420)
            href = item.get("href", "")
            source = item.get("source", "")
            date = item.get("date", "")

            meta = []
            if source:
                meta.append(source)
            if date:
                meta.append(date)
            meta_str = f" [{', '.join(meta)}]" if meta else ""

            lines.append(f"  {rank}. 제목: {title}{meta_str}")
            lines.append(f"     내용: {body}")
            lines.append(f"     링크: {href}\n")

        return "\n".join(lines) + ("\n" if lines else "")


# final_interview_rag.py에서 'from .tool_code import google_search' 로 임포트해 사용
google_search = GoogleSearch()
