# ares/api/services/resume_service.py
from __future__ import annotations
from typing import Dict, Any, List
from dataclasses import dataclass
import json, argparse, sys

from ares.api.utils.ai_utils import chat
from ares.api.utils.common_utils import get_logger, chunk_text

_log = get_logger("resume")

__all__ = [
    "analyze_resume_or_cover",
    "compare_documents",
    "analyze_research_alignment",
]

@dataclass
class GenConfig:
    # 청크링
    chunk_size: int = 8000
    chunk_overlap: int = 600
    max_chunks_analyze: int = 8  # 과도한 분할 방지

    # 토큰 한도
    max_tokens_deep: int = 1100
    max_tokens_cmp: int = 900
    max_tokens_align: int = 900

    # 온도
    t_deep: float = 0.2
    t_cmp: float = 0.2
    t_align: float = 0.3

    # 안전 가드
    max_docs_compare: int = 6     # compare 시 최대 문서 수
    max_chars_per_doc: int = 8000 # compare 입력 per-doc max
    max_jd_chars: int = 8000
    max_resume_chars: int = 9000
    max_research_chars: int = 6000

    # 로깅
    debug_log_prompts: bool = False
CFG = GenConfig()

def _safe_chat(msgs: List[Dict[str,str]], temperature: float, max_tokens: int, default: str="") -> str:
    try:
        out = chat(msgs, temperature=temperature, max_tokens=max_tokens)
        return out or default
    except Exception as e:
        _log.warning(f"LLM 호출 실패: {e}")
        return default

def _inject_company_ctx(prompt: str, meta: Dict[str, Any] | None) -> str:
    if not meta: return prompt
    def _s(x): return (x or "").strip()
    comp = _s(meta.get("company",""))
    div  = _s(meta.get("division",""))
    role = _s(meta.get("role",""))
    loc  = _s(meta.get("location",""))
    kpis = ", ".join([_s(x) for x in meta.get("jd_kpis",[]) if _s(x)])[:200]
    skills = ", ".join([_s(x) for x in meta.get("skills",[]) if _s(x)])[:200]
    ctx = (f"[회사 컨텍스트]\n"
           f"- 회사: {comp or '미상'} | 부서/직무: {div or '-'} / {role or '-'} | 근무지: {loc or '-'}\n"
           f"- KPI: {kpis or '-'} | 스킬: {skills or '-'}\n\n")
    return ctx + prompt

# ---------- 프롬프트(시스템) ----------
SYS_DEEP = (
    "너는 대기업 채용담당자+커리어코치다. 문서를 JD 기준으로 평가·교정한다. "
    "목표: 매칭도 향상, 정량 근거 강화, ATS 통과 가능성 제고. "
    "출력은 한국어, 섹션/불릿 위주, 즉시 반영 가능한 구체 예시 포함. "
    "금지어: '열심히','최대한','많이'. 가능하면 수치/기간/규모/영향 명시."
)
SYS_CMP = (
    "너는 채용담당자다. 여러 문서의 일관성·정합성을 점검/정렬한다. "
    "수치/기간/역할/성과 모순 제거, 스토리라인 정돈, 통일된 표현 예문 제시. 한국어 불릿."
)
SYS_ALIGN = (
    "너는 커리어코치다. JD ↔ 리서치 정합성을 점검해 차별화 포인트/미스매치 리스크/지원서 문장 예시를 제시한다. 한국어 불릿."
)

# ---------- 내부 유틸 ----------
def _dbg(title: str, msgs: List[Dict[str, str]]):
    if not CFG.debug_log_prompts: 
        return
    try:
        _log.debug(f"=== {title} ===\n" + json.dumps(msgs, ensure_ascii=False, indent=2)[:12000])
    except Exception:
        pass

def _label_section(i: int, total: int, content: str) -> str:
    """청크별 결과를 헤더로 구분해 병합."""
    h = f"### [분할 {i}/{total}]\n"
    return h + (content.strip() if content else "")

# ---------- 공개 API ----------
def analyze_resume_or_cover(text: str, jd_text: str = "", meta: Dict[str, Any] | None = None) -> str:
    text = (text or "").strip()
    jd_text = (jd_text or "").strip()

    if not text and not jd_text:
        return "입력된 문서가 없습니다. 이력서/자소서 본문 또는 JD를 제공해주세요."

    chunks: List[str] = list(chunk_text(text, CFG.chunk_size, CFG.chunk_overlap)) if text else [""]
    if len(chunks) > CFG.max_chunks_analyze:
        _log.warning(f"청크 수 초과: {len(chunks)} > {CFG.max_chunks_analyze}, 상위만 분석")
        chunks = chunks[:CFG.max_chunks_analyze]

    results: List[str] = []
    total = len(chunks)
    for i, ch in enumerate(chunks, 1):
        usr = _inject_company_ctx(
            f"[문서 (분할 {i}/{total})]\n{ch}\n\n"
            f"[선택적 JD]\n{jd_text[:CFG.max_jd_chars]}\n\n"
            "요구 출력:\n"
            "1) 핵심 요약(직무연관성 중심)\n"
            "2) JD 매칭도(상/중/하 + 근거: 키워드/경험/지표)\n"
            "3) 키워드 커버리지 표(빠진 키워드 표시)\n"
            "4) STAR 사례(각 S/T/A/R-C 1~2문장 템플릿)\n"
            "5) 정량화 개선안(지표/기간/규모/도구: 예문)\n"
            "6) ATS 리스크/수정안(형식/키워드/중복/가독성)\n"
            "7) 체크리스트(제출 직전 점검)\n",
            meta
        )
        msgs = [{"role": "system", "content": SYS_DEEP},
                {"role": "user", "content": usr}]
        _dbg("analyze_resume_or_cover prompt", msgs)

        out = _safe_chat(msgs, temperature=CFG.t_deep, max_tokens=CFG.max_tokens_deep, default="")
        if out:
            results.append(_label_section(i, total, out))

    return "\n\n".join(results) if results else "평가 생성 실패"

def compare_documents(named_texts: Dict[str, str], meta: Dict[str, Any] | None = None) -> str:
    if not named_texts:
        return "비교할 문서가 없습니다. 최소 1개 이상의 문서를 제공해주세요."

    # 입력 문서 수/길이 가드
    items = list(named_texts.items())[:CFG.max_docs_compare]
    pairs = [f"[{k}]\n{(v or '')[:CFG.max_chars_per_doc]}" for k, v in items]
    joined = "\n\n".join(pairs)

    usr = _inject_company_ctx(
        f"{joined}\n\n"
        "출력:\n"
        "1) 일관성 문제(수치/기간/역할/성과/키워드)\n"
        "2) 모순/누락(증빙 부족/시계열 충돌/책임·성과 불일치)\n"
        "3) 정렬 가이드(우선순위/표현 통일/삭제·추가 권고, 예문)\n"
        "4) 최종 점검표(체크리스트)\n", meta
    )
    msgs = [{"role": "system", "content": SYS_CMP},
            {"role": "user", "content": usr}]
    _dbg("compare_documents prompt", msgs)

    return _safe_chat(msgs, temperature=CFG.t_cmp, max_tokens=CFG.max_tokens_cmp, default="평가 생성 실패")

def analyze_research_alignment(
    jd_text: str,
    resume_concat: str,
    *,
    research_text: str = "",
    meta: Dict[str, Any] | None = None
) -> str:
    """
    입력: JD 본문, 지원서 합본, (선택) 리서치 본문
    출력: JD 요구 vs 지원서 주장 vs (선택) 리서치 팩트의 정합성/리스크/보완 제안 (Markdown)
    - main.py에서는 보통 (jd_text, resume_concat) 두 개만 전달합니다.
    - research_text는 필요할 때 키워드 인자로 넘길 수 있습니다.
    """
    jd_snip   = (jd_text or "")[:CFG.max_jd_chars]
    rs_snip   = (resume_concat or "")[:CFG.max_resume_chars]
    rsch_snip = (research_text or "")[:CFG.max_research_chars]

    if not jd_snip and not rs_snip and not rsch_snip:
        return "분석 대상 텍스트가 없습니다."

    body = f"[JD]\n{jd_snip}\n\n[지원서 합본]\n{rs_snip}\n\n"
    if rsch_snip:
        body += f"[리서치]\n{rsch_snip}\n\n"

    usr = _inject_company_ctx(
        body +
        "출력:\n"
        "1) 핵심 정합성(요구역량↔지원서 주장 연결, KPI/지표 기준)\n"
        "2) 차별화 포인트(회사·직무 포지셔닝)\n"
        "3) 미스매치 리스크(해소 방안)\n"
        "4) 문장 예시(지원서/자소서용 2~3문장 템플릿)\n",
        meta
    )
    msgs = [{"role": "system", "content": SYS_ALIGN},
            {"role": "user", "content": usr}]
    _dbg("analyze_research_alignment prompt", msgs)

    return _safe_chat(msgs, temperature=CFG.t_align, max_tokens=CFG.max_tokens_align, default="평가 생성 실패")

# ---------- CLI: 단일 파일 테스트 ----------
def _cli():
    p = argparse.ArgumentParser(description="Resume service quick test")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_deep = sub.add_parser("deep")
    p_deep.add_argument("--text", required=False, default="")
    p_deep.add_argument("--jd", required=False, default="")

    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("--docs", nargs="+", help="이름=파일경로 ...")

    p_align = sub.add_parser("align")
    p_align.add_argument("--jd", required=False, default="")
    p_align.add_argument("--resume", required=False, default="")
    p_align.add_argument("--research", required=False, default="")

    args = p.parse_args()
    meta = {}  # 필요 시 metadata_service.build_meta_from_inputs(...) 결과 사용

    if args.cmd == "deep":
        print(analyze_resume_or_cover(args.text, jd_text=args.jd, meta=meta))
    elif args.cmd == "compare":
        import os
        named = {}
        for spec in (args.docs or []):
            if "=" in spec:
                name, path = spec.split("=", 1)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        named[name] = f.read()
                except Exception as e:
                    _log.warning(f"파일 로드 실패: {path} | {e}")
        print(compare_documents(named, meta=meta))
    elif args.cmd == "align":
        print(analyze_research_alignment(args.jd, args.resume, research_text=args.research, meta=meta))

if __name__ == "__main__":
    try:
        _cli()
    except Exception as e:
        _log.error(f"resume_service CLI 실패: {e}")
        sys.exit(1)
