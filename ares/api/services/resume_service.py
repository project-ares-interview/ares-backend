# ares/api/services/resume_service.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
import json, argparse, sys
from datetime import date, datetime, timedelta
import re

from ares.api.utils.ai_utils import chat, safe_extract_json
from ares.api.utils.common_utils import get_logger, chunk_text
from ares.api.utils.search_utils import search_ncs_hybrid, format_ncs_context
from ares.api.services.ncs_service import summarize_top_ncs
from ares.api.services.company_data import get_company_description
from ares.api.services.prompts import (
    prompt_jd_preprocessor,
    prompt_jd_classifier,
    prompt_jd_keyword_extractor,
)
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.services.ncs_data import get_ncs_categories, get_ncs_codes

_log = get_logger("resume")

__all__ = [
    "analyze_resume_or_cover",
    "compare_documents",
    "analyze_research_alignment",
    "analyze_all",
]

@dataclass
class GenConfig:
    chunk_size: int = 8000
    chunk_overlap: int = 600
    max_chunks_analyze: int = 8
    max_tokens_deep: int = 1100
    max_tokens_cmp: int = 900
    max_tokens_align: int = 900
    t_deep: float = 0.2
    t_cmp: float = 0.2
    t_align: float = 0.3
    max_docs_compare: int = 6
    max_chars_per_doc: int = 8000
    max_jd_chars: int = 8000
    max_resume_chars: int = 9000
    max_research_chars: int = 6000
    debug_log_prompts: bool = False
CFG = GenConfig()

# ---------- NCS 후처리 필터 로직 ----------
def ncs_post_filter(items: List[Dict], ncs_codes: Dict[str, str], top_k: int = 6) -> Tuple[List[Dict], List[Tuple[Dict, str]]]:
    if not ncs_codes:
        return items[:top_k], []

    out, rejects = [], []
    major_code = ncs_codes.get("major_code")
    middle_code = ncs_codes.get("middle_code")

    for it in items:
        item_major = str(it.get("major_code", "")).strip()
        item_middle = str(it.get("middle_code", "")).strip()

        if item_major == major_code and item_middle == middle_code:
            out.append(it)
        else:
            rejects.append((it, "ncs_code_mismatch"))
        
        if len(out) >= top_k:
            break
            
    return out, rejects

# ---------- 프롬프트(시스템) ----------
SYS_DEEP = (
    "너는 {persona}다. 문서를 JD와 [회사 인재상] 기준으로 평가·교정한다. "
    "목표: 매칭도 향상, 정량 근거 강화, ATS 통과 가능성 제고. "
    "출력은 한국어, 섹션/불릿 위주, 즉시 반영 가능한 구체 예시 포함. "
    "금지어: '열심히','최대한','많이'. 가능하면 수치/기간/규모/영향 명시.\n"
    "[회사 인재상]\n{ideal_candidate_profile}"
)
SYS_CMP = (
    "너는 {persona}다. 제공된 [사전 검증 결과]를 바탕으로, 여러 문서의 일관성·정합성을 자연스러운 문장으로 설명한다. "
    "너의 임무는 주어진 사실을 바탕으로 문장을 생성하는 것이지, 새로운 사실을 판단하는 것이 아니다.\n"
    "[사전 검증 결과]\n{validation_summary}"
)
SYS_ALIGN = (
    "너는 {persona}다. JD ↔ 리서치 정합성을 점검해 차별화 포인트/미스매치 리스크/지원서 문장 예시를 제시한다. 한국어 불릿."
)

class ValidationUtils:
    LANG_VALIDITY_DAYS = {
        "OPIC": 365*2, "OPIc": 365*2, "TOEIC": 365*2, 
        "TOEIC Speaking": 365*2, "TOEIC L&R": 365*2,
    }

    @staticmethod
    def parse_ymd(s: str) -> date | None:
        if not s: return None
        s = s.strip().replace(".", "-").replace("/", "-")
        m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", s)
        if not m: return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
        return date(y, mo, d)

    @staticmethod
    def check_lang_valid(exam_name: str, taken_str: str, reference: date) -> dict:
        taken = ValidationUtils.parse_ymd(taken_str)
        validity = ValidationUtils.LANG_VALIDITY_DAYS.get(exam_name, 365*2)
        if not taken:
            return {"valid": None, "reason": "날짜 파싱 실패"}
        delta = (reference - taken).days
        if delta < 0:
            return {"valid": False, "reason": "미래 날짜"}
        is_valid = delta <= validity
        return {"valid": is_valid, "reason": "유효" if is_valid else "기간 만료"}

def _analyze_jd(jd_text: str, rag_bot: RAGInterviewBot) -> Dict[str, Any]:
    clean_prompt = prompt_jd_preprocessor.format(jd_text=jd_text)
    cleaned_jd = chat([{"role": "user", "content": clean_prompt}], temperature=0.0)

    ncs_categories = get_ncs_categories()
    classify_prompt = prompt_jd_classifier.format(jd_text=cleaned_jd, ncs_categories=json.dumps(ncs_categories, ensure_ascii=False))
    classification_result = chat([{"role": "user", "content": classify_prompt}], temperature=0.0)
    category = safe_extract_json(classification_result).get("category", "Other")
    ncs_codes = get_ncs_codes(category)

    persona = f"20년 경력의 {category} 전문 헤드헌터"
    
    business_summary = rag_bot.base.summarize_company_context(f"Summarize key business areas and strategies for {rag_bot.base.company_name}")
    
    keyword_prompt = prompt_jd_keyword_extractor.format(
        persona=persona,
        jd_text=cleaned_jd,
        business_summary=business_summary
    )
    keyword_result = chat([{"role": "user", "content": keyword_prompt}], temperature=0.1)
    keywords = safe_extract_json(keyword_result).get("keywords", [])
    
    return {"cleaned_jd": cleaned_jd, "category": category, "keywords": keywords, "business_summary": business_summary, "ncs_codes": ncs_codes, "persona": persona}

def _build_ncs_report(meta: Dict[str, Any] | None, jd_ctx: str, jd_keywords: List[str], ncs_codes: Dict[str, str], top: int = 6) -> Tuple[str, Dict]:
    try:
        job_title = ((meta or {}).get("job_title") or "").strip() or "직무명 없음"
        keyword_str = " ".join(jd_keywords)
        query = f"{job_title} {keyword_str}"

        raw_hits = search_ncs_hybrid(query, top=top*4) or []
        hits, _ = ncs_post_filter(raw_hits, ncs_codes=ncs_codes, top_k=top)
        
        agg = summarize_top_ncs(query, jd_ctx, top=top) or []
        ctx_lines = format_ncs_context(hits, max_len=1000)

        structured_context = {"ncs": hits, "ncs_query": query, "jd_keywords": jd_keywords}

        if not agg and not ctx_lines: return "", structured_context

        lines = [f"## 🧩 NCS 요약 (Top {top})", f"- 질의: `{job_title}`", f"- JD 핵심역량: `{', '.join(jd_keywords)}`", ""]
        for i, it in enumerate(agg, 1):
            title = (it.get("ability_name") or it.get("ability_code") or f"Ability-{i}")
            lines.append(f"**{i}. {title}**")
            els = it.get("elements") or []
            if els: lines.append("  - 요소: " + ", ".join(els[:5]))
            samples = it.get("criteria_samples") or []
            for s in samples[:3]: lines.append(f"  - 기준: {s}")

        if ctx_lines:
            lines.append("<details><summary>NCS 컨텍스트(원문 일부)</summary>\n\n" + ctx_lines + "\n</details>\n")

        return "\n".join(lines).strip(), structured_context
    except Exception as e:
        _log.warning(f"NCS 요약 생성 중 오류 발생: {e}")
        return "NCS 요약 생성 중 오류가 발생했습니다.", {}

def analyze_all(jd_text: str, resume_text: str, research_text: str, company_meta: Dict[str, Any]) -> Dict[str, Any]:
    rag_bot = RAGInterviewBot(company_name=company_meta.get("company_name", ""), job_title=company_meta.get("job_title", ""))
    jd_analysis = _analyze_jd(jd_text, rag_bot)
    
    persona = jd_analysis["persona"]
    
    company_name = company_meta.get("company_name", "")
    ideal_candidate_profile = get_company_description(company_name) if company_name else "인재상 정보 없음"

    deep_out = analyze_resume_or_cover(
        resume_text, 
        jd_text=jd_analysis["cleaned_jd"], 
        meta=company_meta, 
        persona=persona,
        ideal_candidate_profile=ideal_candidate_profile
    )
    
    named_texts = {"JD": jd_analysis["cleaned_jd"], "이력서": resume_text}
    cmp_out = compare_documents(named_texts, meta=company_meta, resume_text_raw=resume_text, persona=persona)

    aln_out = ""
    if (research_text or "").strip():
        aln_out = analyze_research_alignment(jd_analysis["cleaned_jd"], resume_text, research_text=research_text, meta=company_meta, persona=persona)

    ncs_out, ncs_ctx = _build_ncs_report(company_meta, jd_analysis["cleaned_jd"], jd_keywords=jd_analysis["keywords"], ncs_codes=jd_analysis["ncs_codes"])

    return {"심층분석": deep_out, "교차분석": cmp_out, "정합성점검": aln_out, "NCS요약": ncs_out, "ncs_context": ncs_ctx}

def analyze_resume_or_cover(text: str, jd_text: str = "", meta: Dict[str, Any] | None = None, persona: str = "대기업 채용담당자+커리어코치", ideal_candidate_profile: str = "인재상 정보 없음") -> str:
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
        usr = f"[문서 (분할 {i}/{total})]\n{ch}\n\n" \
              f"[선택적 JD]\n{jd_text[:CFG.max_jd_chars]}\n\n" \
              "요구 출력:\n" \
              "1) 핵심 요약(직무연관성 중심)\n" \
              "2) JD 및 인재상 매칭도(상/중/하 + 근거: 키워드/경험/지표/인재상 부합)\n" \
              "3) 키워드 커버리지 표(빠진 키워드 표시)\n" \
              "4) STAR 사례(각 S/T/A/R-C 1~2문장 템플릿)\n" \
              "5) 정량화 개선안(지표/기간/규모/도구: 예문)\n" \
              "6) ATS 리스크/수정안(형식/키워드/중복/가독성)\n" \
              "7) 체크리스트(제출 직전 점검)\n"
        
        system_prompt_content = SYS_DEEP.format(
            persona=persona,
            ideal_candidate_profile=ideal_candidate_profile
        )
        msgs = [{"role": "system", "content": system_prompt_content},
                {"role": "user", "content": usr}]
        
        out = chat(msgs, temperature=CFG.t_deep)
        if out:
            results.append(out)

    return "\n\n".join(results) if results else "평가 생성 실패"

def compare_documents(named_texts: Dict[str, str], meta: Dict[str, Any] | None = None, resume_text_raw: str = "", persona: str = "채용담당자") -> str:
    validation_points = []
    # This is a placeholder for actual resume parsing logic
    # In a real scenario, you would parse `resume_text_raw` to find dates and exam names
    # For example:
    # lang_scores = parse_language_scores(resume_text_raw) -> [{"name": "OPIc", "date": "2024-09"}, ...]
    # for score in lang_scores:
    #     check = ValidationUtils.check_lang_valid(score['name'], score['date'], date.today())
    #     validation_points.append(f"- {score['name']} ({score['date']}): {check['reason']}")
    
    validation_summary = "\n".join(validation_points) or "사전 검증된 특이사항 없음."

    items = list(named_texts.items())[:CFG.max_docs_compare]
    pairs = [f"[{k}]\n{(v or '')[:CFG.max_chars_per_doc]}" for k, v in items]
    joined = "\n\n".join(pairs)

    usr = f"{joined}\n\n" \
          "출력:\n" \
          "1) 일관성 문제(수치/기간/역할/성과/키워드)\n" \
          "2) 모순/누락(증빙 부족/시계열 충돌/책임·성과 불일치)\n" \
          "3) 정렬 가이드(우선순위/표현 통일/삭제·추가 권고, 예문)\n" \
          "4) 최종 점검표(체크리스트)\n"
    
    msgs = [{"role": "system", "content": SYS_CMP.format(persona=persona, validation_summary=validation_summary)},
            {"role": "user", "content": usr}]

    return chat(msgs, temperature=CFG.t_cmp)

def analyze_research_alignment(
    jd_text: str,
    resume_concat: str,
    *,
    research_text: str = "",
    meta: Dict[str, Any] | None = None,
    persona: str = "커리어코치",
) -> str:
    jd_snip   = (jd_text or "")[:CFG.max_jd_chars]
    rs_snip   = (resume_concat or "")[:CFG.max_resume_chars]
    rsch_snip = (research_text or "")[:CFG.max_research_chars]

    if not jd_snip and not rs_snip and not rsch_snip:
        return "분석 대상 텍스트가 없습니다."

    body = f"[JD]\n{jd_snip}\n\n[지원서 합본]\n{rs_snip}\n\n"
    if rsch_snip:
        body += f"[리서치]\n{rsch_snip}\n\n"

    usr = body + (
            "출력:\n"
            "1) 핵심 정합성(요구역량↔지원서 주장 연결, KPI/지표 기준)\n"
            "2) 차별화 포인트(회사·직무 포지셔닝)\n"
            "3) 미스매치 리스크(해소 방안)\n"
            "4) 문장 예시(지원서/자소서용 2~3문장 템플릿)\n"
        )
    
    msgs = [{"role": "system", "content": SYS_ALIGN.format(persona=persona)},
            {"role": "user", "content": usr}]

    return chat(msgs, temperature=CFG.t_align)