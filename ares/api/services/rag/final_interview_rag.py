# ares/api/services/rag/final_interview_rag.py
from __future__ import annotations

"""
RAG Interview Bot (Detailed Final Report Edition)

핵심 변경점
- [Init] __init__(..., sync_on_init=False) 추가: 초기 인덱스 동기화 on/off 제어
- [Cache] 회사/직무 사업 요약 캐시(self._bizinfo_cache)
- [Follow-up] 대화 요약에 follow-up Q/A 포함
- [Report] generate_detailed_final_report(): 배치(문항 도시에) → 종합(오버뷰) 멀티-패스
- [Compat] 기존 generate_final_report()도 유지(레거시), 출력 클린업 추가
- [Robust] 프롬프트 주입은 .replace() 일원화, 방어 로직/트렁케이션 유지
"""

import json
import re
import traceback
from typing import Any, Dict, List, Optional

from openai import AzureOpenAI
from unidecode import unidecode
from django.conf import settings

# RAG 시스템
from .new_azure_rag_llamaindex import AzureBlobRAGSystem
# 웹 검색 도구
from .tool_code import google_search
# 프롬프트 (기존 세트)
from ares.api.services.prompt import (
    INTERVIEWER_PERSONAS,
    DIFFICULTY_INSTRUCTIONS,
    prompt_interview_designer,
    prompt_resume_analyzer,
    prompt_rag_answer_analysis,
    prompt_rag_json_correction,
    prompt_rag_follow_up_question,
    prompt_rag_final_report,
    prompt_identifier,
    prompt_extractor,
    prompt_scorer,
    prompt_score_explainer,
    prompt_coach,
    prompt_bias_checker,
    prompt_model_answer,
)
from ares.api.utils.ai_utils import safe_extract_json

import unicodedata


# ============================ 내부 상세 리포트 프롬프트 ============================
_DETAILED_SECTION_PROMPT = """
You are a rigorous interview auditor. Return ONLY valid JSON.

[Goal]
For each Q/A below, produce a detailed dossier including:
- question_intent: why this was asked in this role/company context
- model_answer: an exemplary, structured answer (400~800 chars, framework tagged)
- user_answer_structure: framework extraction and missing elements
- scoring: main/ext scores (0~5 style, integers), rationale, red/green flags
- coaching: strengths, improvements, next steps (actionable)
- additional_followups: 3 precise follow-up questions not asked yet
- fact_checks: claim-by-claim verdicts with brief rationale
- ncs_alignment: relevant NCS titles (if any) and how they map
- risk_notes: any hiring risks signaled by the answer

[Context]
- company: {company_name}
- role: {job_title}
- persona: {persona_description}
- evaluation_focus: {evaluation_focus}
- business_info: {business_info}
- ncs_titles: {ncs_titles}

[InputItems]
{items}

[Output JSON Schema]
{
  "per_question_dossiers": [
    {
      "question_id": "1-1",
      "question": "...",
      "question_intent": "...",
      "model_answer": "...",
      "user_answer_structure": {
        "framework": "STAR|CASE|SYSTEMDESIGN|COMPETENCY|OTHER",
        "elements_present": ["..."],
        "elements_missing": ["..."]
      },
      "scoring": {
        "applied_framework": "STAR",
        "scores_main": {"clarity": 0, "depth": 0, "evidence": 0, "relevance": 0},
        "scores_ext": {"leadership": 0, "communication": 0, "metrics": 0},
        "scoring_reason": "..."
      },
      "coaching": {
        "strengths": ["..."],
        "improvements": ["..."],
        "next_steps": ["..."]
      },
      "additional_followups": ["Q1","Q2","Q3"],
      "fact_checks": [{"claim":"...","verdict":"지원|불충분|반박","rationale":"..."}],
      "ncs_alignment": ["..."],
      "risk_notes": ["..."]
    }
  ]
}
"""

_DETAILED_OVERVIEW_PROMPT = """
You are a head interviewer producing a FINAL exhaustive interview report. Return ONLY valid JSON.

[Goal]
Merge per-question dossiers, the interview plan, resume feedback, and transcript to produce:
- overall_summary (2~4 paragraphs)
- interview_flow_rationale: why this sequence made sense; what was tested each stage
- strengths_matrix: thematic clusters with evidence refs (question_ids)
- weaknesses_matrix: same as above, with risk severity
- score_aggregation: averages, spread, calibration notes
- missed_opportunities: what strong answers were expected but missing
- potential_followups_global: 5-10 best follow-ups not yet asked
- resume_feedback (verbatim or summarized if too long)
- hiring_recommendation: "strong_hire|hire|no_hire" with explicit reasons
- next_actions: concrete steps before offer/no-offer (e.g., reference checks, take-home)
- question_by_question_feedback: per-question cards (intent/model answer/followups)

[Context]
- company: {company_name}
- role: {job_title}
- persona: {persona_description}
- final_report_goal: {final_report_goal}
- evaluation_focus: {evaluation_focus}

[Inputs]
- interview_plan: {interview_plan_json}
- resume_feedback_analysis: {resume_feedback_json}
- transcript_digest: {transcript_digest}
- per_question_dossiers: {per_question_dossiers}

[Output JSON Schema]
{
  "overall_summary": "...",
  "interview_flow_rationale": "...",
  "strengths_matrix": [{"theme":"...","evidence":["1-2","2-1"]}],
  "weaknesses_matrix": [{"theme":"...","severity":"low|medium|high","evidence":["..."]}],
  "score_aggregation": {
    "main_avg": {},
    "ext_avg": {},
    "calibration": "..."
  },
  "missed_opportunities": ["..."],
  "potential_followups_global": ["..."],
  "resume_feedback": {
    "job_fit_assessment": "...",
    "strengths_and_opportunities": "...",
    "gaps_and_improvements": "..."
  },
  "hiring_recommendation": "strong_hire|hire|no_hire",
  "next_actions": ["..."],
  "question_by_question_feedback": [
    {
      "question_id": "1-1",
      "stage": "...",
      "objective": "...",
      "question": "...",
      "question_intent": "...",
      "evaluation": {
        "applied_framework": "STAR",
        "scores_main": {},
        "scores_ext": {},
        "feedback": "..."
      },
      "model_answer": "...",
      "additional_followups": ["..."]
    }
  ]
}
"""


# ================================ 유틸리티 ================================
def _escape_special_chars(text: str) -> str:
    """Azure AI Search/Lucene 예약문자 이스케이프"""
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
                parts = re.split(r"(?<=[.!?])\s+", q)
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
    raw2 = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)
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
                m = re.split(r"(?<=[.!?])\s+", q)
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


# ================================ 본체 ================================
class RAGInterviewBot:
    """RAG + LLM 기반 구조화 면접 Bot (상세 리포트 확장판)"""

    def __init__(
        self,
        company_name: str,
        job_title: str,
        container_name: str,
        index_name: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",
        ncs_context: Optional[dict] = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        *,
        sync_on_init: bool = False,   # 🔹 초기 인덱스 동기화 on/off
        **kwargs,
    ):
        print(f"🤖 RAG 전용 사업 분석 면접 시스템 초기화 (면접관: {interviewer_mode})...")
        self.company_name = company_name or "알수없음회사"
        self.job_title = job_title or "알수없음직무"
        self.difficulty = difficulty
        self.interviewer_mode = interviewer_mode
        self.ncs_context = ncs_context or {}
        self.jd_context = _truncate(jd_context, 4000)
        self.resume_context = _truncate(resume_context, 4000)
        self.research_context = _truncate(research_context, 4000)

        self.persona = INTERVIEWER_PERSONAS.get(self.interviewer_mode, INTERVIEWER_PERSONAS["team_lead"])

        self.endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", None)
        self.api_key = getattr(settings, "AZURE_OPENAI_KEY", None)
        self.api_version = (
            getattr(settings, "AZURE_OPENAI_API_VERSION", None)
            or getattr(settings, "API_VERSION", None)
            or "2024-08-01-preview"
        )
        self.model = (
            getattr(settings, "AZURE_OPENAI_MODEL", None)
            or getattr(settings, "AZURE_OPENAI_DEPLOYMENT", None)
            or "gpt-4o"
        )
        if not self.endpoint or not self.api_key:
            raise ValueError("Azure OpenAI endpoint/key is not set in Django settings.")

        self.client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version,
        )

        print("\n📊 Azure 사업 분석 RAG 시스템 연동...")
        self.rag_system = None
        self.rag_ready = False
        self._bizinfo_cache: Dict[str, str] = {}  # 🔹 회사/직무별 요약 캐시

        try:
            self.rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            blobs = list(self.rag_system.container_client.list_blobs())
            if not blobs:
                print(f"⚠️ 경고: Azure Blob 컨테이너 '{container_name}'에 분석할 파일이 없습니다.")
                return

            print(f"✅ Azure RAG 시스템 준비 완료. {len(blobs)}개의 문서를 기반으로 합니다.")
            if sync_on_init:
                print("🔄 Azure AI Search 인덱스 자동 동기화 시작...(sync_on_init=True)")
                self.rag_system.sync_index(company_name_filter=self.company_name)
            else:
                print("⏩ 인덱스 동기화 생략(sync_on_init=False) — 필요 시 외부 엔드포인트/관리자에서 수행")

            self.rag_ready = True

        except Exception as e:
            print(f"❌ RAG 시스템 연동 실패: {e}")

    # ----------------------------- 내부 LLM 호출 -----------------------------
    def _chat_json(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2000) -> str:
        """JSON 전용 응답 강제(미지원시 폴백 재시도)"""
        sys_msg = {"role": "system", "content": "You must return ONLY a single valid JSON object. No markdown/code fences/commentary."}
        messages = [sys_msg, {"role": "user", "content": prompt}]
        kwargs = dict(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens)

        try:
            kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[sys_msg, {"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON."}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()

    def _chat_text(self, prompt: str, temperature: float = 0.4, max_tokens: int = 300) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    # ----------------------------- [신규] RAG 헬퍼 -----------------------------
    def _get_company_business_info(self) -> str:
        """RAG에서 회사/직무 핵심 정보 조회 요약(인스턴스 캐시)."""
        if not self.rag_ready:
            return ""
        try:
            cache_key = f"{self.company_name}::{self.job_title}"
            if cache_key in self._bizinfo_cache:
                return self._bizinfo_cache[cache_key]

            safe_company_name = _escape_special_chars(self.company_name)
            safe_job_title = _escape_special_chars(self.job_title)
            query_text = f"{safe_company_name}의 핵심 사업, 최근 실적, 주요 리스크, 그리고 {safe_job_title} 직무와 관련된 회사 정보에 대해 요약해줘."
            print(f"🔍 '{self.rag_system.index_name}' 인덱스에서 회사 정보 조회: {query_text}")
            business_info_raw = self.rag_system.query(query_text)
            summary = _truncate(business_info_raw or "", 1200)
            self._bizinfo_cache[cache_key] = summary
            return summary
        except Exception as e:
            print(f"⚠️ 회사 정보 조회 실패: {e}")
            return ""

    # ----------------------------- 플랜 생성 -----------------------------
    def design_interview_plan(self) -> Dict:
        if not self.rag_ready:
            return {"error": "RAG 시스템이 준비되지 않았습니다."}

        print(f"\n🧠 {self.company_name} 맞춤 면접 계획 설계 중 (난이도: {self.difficulty}, 면접관: {self.interviewer_mode})...")
        try:
            business_info = self._get_company_business_info()

            # NCS 요약 문자열
            ncs_info = ""
            if isinstance(self.ncs_context.get("ncs"), list):
                ncs_titles = [item.get("title") for item in self.ncs_context["ncs"] if item.get("title")]
                if ncs_titles:
                    ncs_info = f"\n\nNCS 직무 관련 정보: {', '.join(ncs_titles[:6])}."

            persona_description = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(self.difficulty, "")

            prompt = (
                prompt_interview_designer
                .replace("{persona_description}", persona_description)
                .replace("{question_style_guide}", self.persona["question_style_guide"])
                .replace("{company_name}", self.company_name)
                .replace("{job_title}", self.job_title)
                .replace("{difficulty_instruction}", difficulty_instruction)
                .replace("{business_info}", business_info)
                .replace("{jd_context}", _truncate(self.jd_context, 1200))
                .replace("{resume_context}", _truncate(self.resume_context, 1200))
                .replace("{research_context}", _truncate(self.research_context, 1200))
                .replace("{ncs_info}", _truncate(ncs_info, 400))
            )

            raw = self._chat_json(prompt, temperature=0.3, max_tokens=3200)
            parsed = safe_extract_json(raw) or _force_json_like(raw) or {}
            normalized = _normalize_plan_local(parsed)

            # 교정 패스
            if not normalized:
                _debug_print_raw_json("PLAN_FIRST_PASS", raw)
                correction_raw = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": prompt_rag_json_correction},
                    ],
                    temperature=0.0,
                    max_tokens=2000,
                    response_format={"type": "json_object"},
                ).choices[0].message.content or ""
                corrected = safe_extract_json(correction_raw) or _force_json_like(correction_raw) or {}
                normalized = _normalize_plan_local(corrected)
                if not normalized:
                    _debug_print_raw_json("PLAN_CORRECTION_FAILED", correction_raw)

            # 폴백: 오프닝 1문장
            if not normalized:
                print("ℹ️ 플랜 정규화 실패 → 오프닝 최소 플랜 구성.")
                single = self.generate_opening_question(
                    company_name=self.company_name,
                    job_title=self.job_title,
                    difficulty=self.difficulty,
                    context_hint={"business_info": business_info},
                )
                if single:
                    normalized = [{
                        "stage": "Opening",
                        "objective": "지원자의 기본 역량과 사고방식 검증",
                        "questions": [single],
                    }]

            print("✅ 구조화 면접 계획 수립 완료." if normalized else "⚠️ 구조화 면접 계획이 비어있음.")
            return {"interview_plan": normalized}

        except Exception as e:
            error_msg = f"면접 계획 수립 실패: {e}"
            print(f"❌ {error_msg}")
            traceback.print_exc()
            return {"error": error_msg}

    # ----------------------------- 오프닝 폴백 -----------------------------
    def generate_opening_question(
        self,
        company_name: str,
        job_title: str,
        difficulty: str,
        context_hint: Optional[Dict] = None,
    ) -> str:
        hints = []
        if isinstance(context_hint, dict):
            bi = context_hint.get("business_info")
            if bi:
                hints.append(str(bi)[:600])
        ncs_titles = [it.get("title") for it in (self.ncs_context or {}).get("ncs", []) if it.get("title")]
        if ncs_titles:
            hints.append("NCS: " + ", ".join(ncs_titles[:5]))

        prompt = (
            f"[역할] 당신은 {company_name} {job_title} 면접의 {self.interviewer_mode} 면접관\n"
            f"[난이도] {difficulty}\n"
            "[요청] 지원자의 역량을 검증할 '오프닝 질문' 1문장만 출력.\n"
            "모호한 표현을 피하고, 수치/근거/사례 제시를 유도할 것.\n"
            f"[힌트]\n- " + ("\n- ".join(hints) if hints else "(없음)")
        )
        try:
            text = self._chat_text(prompt, temperature=0.4, max_tokens=200)
            return text.strip().split("\n")[0].strip()
        except Exception as e:
            print(f"❌ 단건 오프닝 질문 생성 실패: {e}")
            return ""

    # ----------------------------- 이력서/RAG 비교 -----------------------------
    def analyze_resume_with_rag(self) -> Dict:
        if not self.rag_ready:
            return {"error": "RAG 시스템이 준비되지 않았습니다."}
        if not self.resume_context:
            return {"error": "분석할 이력서 정보가 없습니다."}

        print(f"\n📄 RAG 기반 이력서 분석 중 (면접관: {self.interviewer_mode})...")
        try:
            business_info = self._get_company_business_info()

            persona_description = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            prompt = (
                prompt_resume_analyzer
                .replace("{persona_description}", persona_description)
                .replace("{company_name}", self.company_name)
                .replace("{job_title}", self.job_title)
                .replace("{business_info}", business_info)
                .replace("{resume_context}", _truncate(self.resume_context, 1200))
            )

            raw = self._chat_json(prompt, temperature=0.3, max_tokens=1500)
            result = safe_extract_json(raw) or {}
            print("✅ 이력서-회사 연관성 분석 완료.")
            return result
        except Exception as e:
            error_msg = f"이력서 분석 실패: {e}"
            print(f"❌ {error_msg}")
            return {"error": error_msg}

    # ----------------------------- 구조화 평가 파이프라인 -----------------------------
    def _structured_evaluation(self, role: str, answer: str) -> Dict:
        """Identifier → Extractor → Scorer → ScoreExplainer → Coach → ModelAnswer → BiasChecker"""
        try:
            # 1) Identifier
            id_prompt = prompt_identifier.replace("{answer}", _truncate(answer, 1800))
            id_raw = self._chat_json(id_prompt, temperature=0.1, max_tokens=800)
            id_json = safe_extract_json(id_raw) or {}
            frameworks: List[str] = id_json.get("frameworks", []) if isinstance(id_json, dict) else []
            values_summary = id_json.get("company_values_summary", "")

            # 기본 프레임워크 추정
            base_fw = None
            for fw in frameworks:
                if isinstance(fw, str):
                    base_fw = (fw.split("+")[0] or "").upper().strip()
                    if base_fw:
                        break
            if not base_fw:
                base_fw = "STAR"

            # 2) Extractor
            component_map = {
                "STAR": ["situation", "task", "action", "result"],
                "SYSTEMDESIGN": ["requirements", "trade_offs", "architecture", "risks"],
                "CASE": ["problem", "structure", "analysis", "recommendation"],
                "COMPETENCY": ["competency", "behavior", "impact"],
            }
            component_list = json.dumps(component_map.get(base_fw, []), ensure_ascii=False)
            extractor_prompt = (
                prompt_extractor
                .replace("{component_list}", component_list)
                .replace("{analysis_key}", "extracted")
                .replace("{framework_name}", base_fw)
                + "\n[지원자 답변 원문]\n"
                + _truncate(answer, 1800)
            )
            ex_raw = self._chat_json(extractor_prompt, temperature=0.2, max_tokens=1600)
            ex_json = safe_extract_json(ex_raw) or {}

            # 3) Scorer
            ncs_titles = [item.get("title") for item in self.ncs_context.get("ncs", []) if item.get("title")] if isinstance(self.ncs_context.get("ncs"), list) else []
            ncs_details = _truncate(", ".join(ncs_titles), 1200)
            persona_desc_scorer = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            scorer_prompt = (
                prompt_scorer
                .replace("{framework_name}", base_fw)
                .replace("{retrieved_ncs_details}", ncs_details)
                .replace("{role}", role)
                .replace("{persona_description}", persona_desc_scorer)
                .replace("{evaluation_focus}", self.persona["evaluation_focus"])
                + "\n[지원자 답변 원문]\n"
                + _truncate(answer, 1800)
            )
            sc_raw = self._chat_json(scorer_prompt, temperature=0.2, max_tokens=1500)
            sc_json = safe_extract_json(sc_raw) or {}

            # 4) Score Explainer
            persona_desc_explainer = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            expl_prompt = (
                prompt_score_explainer
                .replace("{framework}", json.dumps(sc_json.get("framework", base_fw), ensure_ascii=False))
                .replace("{scores_main}", json.dumps(sc_json.get("scores_main", {}), ensure_ascii=False))
                .replace("{scores_ext}", json.dumps(sc_json.get("scores_ext", {}), ensure_ascii=False))
                .replace("{scoring_reason}", _truncate(sc_json.get("scoring_reason", ""), 800))
                .replace("{role}", role)
                .replace("{persona_description}", persona_desc_explainer)
            )
            expl_raw = self._chat_json(expl_prompt, temperature=0.2, max_tokens=2000)
            expl_json = safe_extract_json(expl_raw) or {}

            # 5) Coach
            persona_desc_coach = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            coach_prompt = (
                prompt_coach
                .replace("{persona_description}", persona_desc_coach)
                .replace("{scoring_reason}", _truncate(sc_json.get("scoring_reason", ""), 800))
                .replace("{user_answer}", _truncate(answer, 1800))
                .replace("{retrieved_ncs_details}", ncs_details)
                .replace("{role}", role)
                .replace("{company_name}", self.company_name)
            )
            coach_raw = self._chat_json(coach_prompt, temperature=0.2, max_tokens=1400)
            coach_json = safe_extract_json(coach_raw) or {}

            # 6) Model Answer
            persona_desc_model = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            model_prompt = (
                prompt_model_answer
                .replace("{persona_description}", persona_desc_model)
                .replace("{retrieved_ncs_details}", ncs_details)
            )
            model_raw = self._chat_json(model_prompt, temperature=0.4, max_tokens=1400)
            model_json = safe_extract_json(model_raw) or {}

            # 7) Bias Checker
            def bias_sanitize(text: str) -> Dict:
                bprompt = prompt_bias_checker.replace("{any_text}", _truncate(text or "", 1600))
                braw = self._chat_json(bprompt, temperature=0.0, max_tokens=1400)
                return safe_extract_json(braw) or {}

            coach_text = json.dumps(coach_json, ensure_ascii=False)
            model_text = json.dumps(model_json, ensure_ascii=False)
            coach_bias = bias_sanitize(coach_text)
            model_bias = bias_sanitize(model_text)

            return {
                "identifier": {"frameworks": frameworks, "company_values_summary": values_summary},
                "extracted": ex_json.get("extracted") if isinstance(ex_json, dict) else ex_json,
                "scoring": sc_json,
                "calibration": expl_json,
                "coach": coach_json if not coach_bias.get("flagged") else coach_bias.get("sanitized_text", coach_json),
                "coach_bias_issues": coach_bias.get("issues", []),
                "model_answer": model_json if not model_bias.get("flagged") else model_bias.get("sanitized_text", model_json),
                "model_bias_issues": model_bias.get("issues", []),
            }
        except Exception as e:
            print(f"❌ 구조화 평가 파이프라인 오류: {e}")
            traceback.print_exc()
            return {"error": f"structured_evaluation_failed: {e}"}

    # ----------------------------- RAG 서술형 평가 -----------------------------
    def _rag_narrative_analysis(self, question: str, answer: str) -> Dict:
        if not self.rag_ready:
            return {"error": "RAG 시스템 미준비"}

        try:
            # 웹 결과 (best effort)
            try:
                web_result = google_search.search(queries=[f"{self.company_name} {answer}"])
                if not isinstance(web_result, str):
                    web_result = _truncate(json.dumps(web_result, ensure_ascii=False), 2000)
            except Exception:
                web_result = "검색 실패 또는 결과 없음"

            safe_answer = _escape_special_chars(answer)
            internal_check_raw = self.rag_system.query(
                f"'{safe_answer}'라는 주장에 대한 사실관계를 확인하고 관련 데이터를 찾아줘."
            )
            internal_check = _truncate(internal_check_raw or "", 1200)

            persona_desc = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            analysis_prompt = (
                prompt_rag_answer_analysis
                .replace("{persona_description}", persona_desc)
                .replace("{evaluation_focus}", self.persona["evaluation_focus"])
                .replace("{company_name}", self.company_name)
                .replace("{question}", _truncate(question, 400))
                .replace("{answer}", _truncate(answer, 1500))
                .replace("{internal_check}", internal_check)
                .replace("{web_result}", _truncate(web_result, 1500))
            )

            raw_json = self._chat_json(analysis_prompt, temperature=0.2, max_tokens=2000)
            result = safe_extract_json(raw_json)
            if result is not None:
                return result

            # 자가 교정
            _debug_print_raw_json("RAG_FIRST_PASS", raw_json or "")
            corrected_raw = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                    {"role": "user", "content": analysis_prompt},
                    {"role": "assistant", "content": raw_json},
                    {"role": "user", "content": prompt_rag_json_correction},
                ],
                temperature=0.0,
                max_tokens=2000,
                response_format={"type": "json_object"},
            ).choices[0].message.content or ""
            final_result = safe_extract_json(corrected_raw)
            if final_result is not None:
                return final_result
            _debug_print_raw_json("RAG_CORRECTION_FAILED", corrected_raw)
            return {"error": "Failed to parse AI response after correction"}

        except Exception as e:
            print(f"❌ RAG 서술형 평가 실패: {e}")
            traceback.print_exc()
            return {"error": f"Failed to analyze answer (RAG): {e}"}

    # ----------------------------- 공개 메서드: 답변 분석 -----------------------------
    def analyze_answer_with_rag(self, question: str, answer: str, role: Optional[str] = None) -> Dict:
        role = role or self.job_title
        print(f"    (답변 분석 중... 면접관: {self.interviewer_mode})")
        structured = self._structured_evaluation(role=role, answer=answer)
        rag_analysis = self._rag_narrative_analysis(question=question, answer=answer)
        return {"structured": structured, "rag_analysis": rag_analysis}

    # ----------------------------- 출력 포매터 (CLI) -----------------------------
    def print_individual_analysis(self, analysis: Dict, question_num: str):
        if "error" in analysis:
            print(f"\n❌ 분석 오류: {analysis['error']}")
            return

        print("\n" + "=" * 70)
        print(f"📊 [{question_num}] 답변 상세 분석 결과")
        print("=" * 70)

        # RAG Narrative
        rag = analysis.get("rag_analysis", {})
        print("\n" + "-" * 30)
        print("✅ 주장별 사실 확인 (RAG - Fact-Checking)")
        checks = (rag or {}).get("claims_checked", [])
        if not checks:
            print("  - 확인된 주장이 없습니다.")
        else:
            for c in checks:
                claim = c.get("claim", "N/A")
                verdict = c.get("verdict") or "N/A"
                src = c.get("evidence_source", "")
                rationale = c.get("rationale") or "N/A"
                print(f'  - 주장: "{claim}"')
                print(f'    - 판정: {verdict} {f"({src})" if src else ""}')
                print(f'    - 근거: {rationale}')

        print("\n" + "-" * 30)
        print("📝 내용 분석 (RAG - Narrative)")
        summary = (rag or {}).get("analysis", "")
        print(f"  - 요약: {summary or 'N/A'}")

        print("\n" + "-" * 30)
        print("💡 실행 가능한 피드백 (RAG - Actionable)")
        fb = (rag or {}).get("feedback", "")
        print(f"  - {fb or '피드백 없음'}")

        # Structured
        st = analysis.get("structured", {})
        print("\n" + "-" * 30)
        print("📐 구조화 채점 요약 (Structured Scoring)")
        sc = st.get("scoring", {})
        if sc:
            print(f"  - Framework: {sc.get('framework', 'N/A')}")
            print(f"  - Main: {json.dumps(sc.get('scores_main', {}), ensure_ascii=False)}")
            print(f"  - Ext : {json.dumps(sc.get('scores_ext', {}), ensure_ascii=False)}")
        else:
            print("  - 채점 결과 없음")

        expl = st.get("calibration", {})
        if expl:
            tip = expl.get("overall_tip", "")
            print("  - 캘리브레이션 Tip:", tip or "N/A")

        coach = st.get("coach")
        if coach:
            print("\n  - 코칭(강점/개선/총평) 제공됨")

    # ----------------------------- 꼬리 질문 생성 -----------------------------
    def generate_follow_up_question(self, original_question: str, answer: str, analysis: Dict, stage: str, objective: str) -> str:
        try:
            # 결핍 힌트 추출(코칭 개선점/캘리브레이션 gap)
            deficit_parts: List[str] = []
            st = analysis.get("structured", {}) if isinstance(analysis, dict) else {}
            coach = st.get("coach", {})
            if isinstance(coach, dict):
                deficit_parts += (coach.get("improvements") or [])[:3]
            calib = st.get("calibration", {})
            if isinstance(calib, dict):
                for item in calib.get("calibration", [])[:2]:
                    if isinstance(item, dict) and item.get("gap"):
                        deficit_parts.append(f"{item.get('element','요소')} gap={item.get('gap')}")
            deficit_hint = _truncate(" / ".join(deficit_parts), 240)

            persona_desc = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            prompt = (
                prompt_rag_follow_up_question
                .replace("{persona_description}", persona_desc)
                .replace("{company_name}", self.company_name)
                .replace("{stage}", stage)
                .replace("{objective}", objective or "")
                .replace("{deficit_hint}", deficit_hint)
                + "\n[기존 질문]\n" + _truncate(original_question, 300)
                + "\n[지원자 답변]\n" + _truncate(answer, 600)
            )
            raw = self._chat_json(prompt, temperature=0.6, max_tokens=300)
            result = safe_extract_json(raw)
            return (result or {}).get("follow_up_question", "")
        except Exception as e:
            print(f"❌ 꼬리 질문 생성 실패: {e}")
            return ""

    # ----------------------------- CLI 면접 시나리오 -----------------------------
    def conduct_interview(self):
        if not self.rag_ready:
            print("\n❌ RAG 시스템이 준비되지 않아 면접을 진행할 수 없습니다.")
            return

        resume_analysis = self.analyze_resume_with_rag()
        interview_plan_data = self.design_interview_plan()
        if "error" in interview_plan_data:
            print(f"\n❌ {interview_plan_data['error']}")
            return
        plan = interview_plan_data.get("interview_plan")
        if not plan:
            print("\n❌ 면접 계획을 수립하지 못했습니다.")
            return
        interview_plan = plan

        print("\n" + "=" * 70)
        print(f"🏢 {self.company_name} {self.job_title} 직무 {self.interviewer_mode} 면접을 시작하겠습니다.")
        print("면접은 단계별 질문으로 진행됩니다. 종료하려면 /quit 입력.")
        print("=" * 70)

        interview_transcript: List[Dict] = []
        interview_stopped = False

        for i, stage_data in enumerate(interview_plan, 1):
            stage_name = stage_data.get("stage", f"단계 {i}")
            objectives = stage_data.get("objectives") or stage_data.get("objective")
            stage_objective = objectives[0] if isinstance(objectives, list) and objectives else (objectives or "N/A")
            questions = stage_data.get("questions", [])

            print(f"\n\n--- 면접 단계 {i}: {stage_name} ---")
            print(f"🎯 이번 단계의 목표: {stage_objective}")

            for q_idx, question in enumerate(questions, 1):
                question_id = f"{i}-{q_idx}"
                print(f"\n--- [질문 {question_id}] ---")
                print(f"👨‍💼 면접관: {question}")
                answer = input("💬 답변: ")

                if answer.lower() in ["/quit", "/종료"]:
                    interview_stopped = True
                    break

                analysis = self.analyze_answer_with_rag(question, answer, role=self.job_title)
                follow_up_question = ""
                follow_up_answer = ""
                if analysis and "error" not in analysis:
                    follow_up_question = self.generate_follow_up_question(
                        original_question=question,
                        answer=answer,
                        analysis=analysis,
                        stage=stage_name,
                        objective=stage_objective
                    )
                    if follow_up_question:
                        print("\n--- [꼬리 질문] ---")
                        print(f"👨‍💼 면접관: {follow_up_question}")
                        follow_up_answer = input("💬 답변: ")

                interview_transcript.append({
                    "question_id": question_id,
                    "stage": stage_name,
                    "objective": stage_objective,
                    "question": question,
                    "answer": answer,
                    "analysis": analysis,
                    "follow_up_question": follow_up_question,
                    "follow_up_answer": follow_up_answer
                })

            if interview_stopped:
                break

        print("\n🎉 면접이 종료되었습니다. 수고하셨습니다.")

        if interview_transcript:
            self._generate_and_print_reports(interview_transcript, interview_plan_data, resume_analysis)

    # ----------------------------- 리포트 생성/출력 -----------------------------
    def _cleanup_assessments(self, report: Dict) -> Dict:
        """assessment 필드 꼬리 콤마 등 간단 정리."""
        try:
            comps = report.get("core_competency_analysis", [])
            for c in comps:
                a = c.get("assessment")
                if isinstance(a, str):
                    c["assessment"] = a.replace(",", "").strip()
        except Exception:
            pass
        return report

    def _generate_and_print_reports(self, transcript, plan_data, resume_analysis):
        print("\n\n" + "#" * 70)
        print(" 면접 전체 답변에 대한 상세 분석 리포트")
        print("#" * 70)

        for item in transcript:
            self.print_individual_analysis(item["analysis"], item["question_id"])

        # 멀티-패스 상세 리포트 생성
        report = self.generate_detailed_final_report(
            transcript=transcript,
            interview_plan=plan_data,
            resume_feedback_analysis=resume_analysis,
            batch_size=4,
            max_transcript_digest_chars=7000
        )
        self.print_final_report(report)

    def generate_detailed_final_report(
        self,
        transcript: List[Dict],
        interview_plan: Dict,
        resume_feedback_analysis: Dict,
        batch_size: int = 4,
        max_transcript_digest_chars: int = 6000,
    ) -> Dict:
        """문항 배치 도시에 → 오버뷰 종합 패스(원하던 수준의 리치 리포트)."""
        if not transcript:
            return {"error": "empty_transcript"}

        persona_desc = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
        business_info = self._get_company_business_info()
        ncs_titles = []
        if isinstance(self.ncs_context.get("ncs"), list):
            ncs_titles = [it.get("title") for it in self.ncs_context["ncs"] if it.get("title")]

        # transcript digest (follow-up 포함, 길이 상향)
        digest_lines = []
        for item in transcript:
            qid = item.get("question_id", "N/A")
            stage = item.get("stage", "N/A")
            obj = item.get("objective", "")
            rag_analysis = (item.get("analysis") or {}).get("rag_analysis", {})
            analysis_line = rag_analysis.get("analysis") or ""
            if not analysis_line:
                stc = (item.get("analysis") or {}).get("structured", {}).get("scoring", {})
                analysis_line = stc.get("scoring_reason", "")
            digest_lines.append(
                f"[{stage}] {qid} Q: {item.get('question','')}\n"
                f"  A: {_truncate(item.get('answer',''), 500)}\n"
                f"  Σ: {_truncate(analysis_line or '없음', 600)}"
            )
            if item.get("follow_up_question"):
                digest_lines.append(
                    f"  FU-Q: {item['follow_up_question']}\n"
                    f"  FU-A: {_truncate(item.get('follow_up_answer',''), 320)}"
                )
            if obj:
                digest_lines.append(f"  ▶Objective: {obj}")
            digest_lines.append("---")
        transcript_digest = _truncate("\n".join(digest_lines), max_transcript_digest_chars)

        # per-question dossiers — 배치 생성
        per_question_dossiers: List[Dict] = []
        for batch in _chunked(transcript, batch_size):
            items_blob = []
            for it in batch:
                items_blob.append({
                    "question_id": it.get("question_id", ""),
                    "stage": it.get("stage", ""),
                    "objective": it.get("objective", ""),
                    "question": it.get("question", ""),
                    "user_answer": _truncate(it.get("answer", ""), 1800),
                    "analysis_hint": {
                        "structured": it.get("analysis", {}).get("structured", {}),
                        "rag": it.get("analysis", {}).get("rag_analysis", {})
                    },
                    "follow_up_asked": it.get("follow_up_question") or "",
                    "follow_up_answer": _truncate(it.get("follow_up_answer", ""), 800)
                })
            prompt = _DETAILED_SECTION_PROMPT \
                .replace("{company_name}", self.company_name) \
                .replace("{job_title}", self.job_title) \
                .replace("{persona_description}", persona_desc) \
                .replace("{evaluation_focus}", self.persona["evaluation_focus"]) \
                .replace("{business_info}", _truncate(business_info, 1000)) \
                .replace("{ncs_titles}", _truncate(", ".join(ncs_titles), 500)) \
                .replace("{items}", json.dumps(items_blob, ensure_ascii=False))

            raw = self._chat_json(prompt, temperature=0.2, max_tokens=3800)
            part = safe_extract_json(raw) or _force_json_like(raw) or {}
            part_list = part.get("per_question_dossiers", []) if isinstance(part, dict) else []
            if not isinstance(part_list, list):
                part_list = []
            per_question_dossiers.extend(part_list)

        # 오버뷰 종합
        overview_prompt = _DETAILED_OVERVIEW_PROMPT \
            .replace("{company_name}", self.company_name) \
            .replace("{job_title}", self.job_title) \
            .replace("{persona_description}", persona_desc) \
            .replace("{final_report_goal}", self.persona["final_report_goal"]) \
            .replace("{evaluation_focus}", self.persona["evaluation_focus"]) \
            .replace("{interview_plan_json}", _truncate(json.dumps(interview_plan, ensure_ascii=False), 6000)) \
            .replace("{resume_feedback_json}", _truncate(json.dumps(resume_feedback_analysis, ensure_ascii=False), 6000)) \
            .replace("{transcript_digest}", transcript_digest) \
            .replace("{per_question_dossiers}", json.dumps(per_question_dossiers, ensure_ascii=False))

        raw_final = self._chat_json(overview_prompt, temperature=0.25, max_tokens=4000)
        final = safe_extract_json(raw_final) or _force_json_like(raw_final) or {}

        # 클린업
        try:
            qitems = final.get("question_by_question_feedback", [])
            for qi in qitems:
                ev = qi.get("evaluation", {})
                if isinstance(ev, dict):
                    reason = ev.get("feedback") or ev.get("scoring_reason") or ""
                    if isinstance(reason, str):
                        ev["feedback"] = reason.strip()
        except Exception:
            pass

        return {
            "overall_summary": final.get("overall_summary", ""),
            "interview_flow_rationale": final.get("interview_flow_rationale", ""),
            "strengths_matrix": final.get("strengths_matrix", []),
            "weaknesses_matrix": final.get("weaknesses_matrix", []),
            "score_aggregation": final.get("score_aggregation", {}),
            "missed_opportunities": final.get("missed_opportunities", []),
            "potential_followups_global": final.get("potential_followups_global", []),
            "resume_feedback": final.get("resume_feedback", resume_feedback_analysis),
            "hiring_recommendation": final.get("hiring_recommendation", ""),
            "next_actions": final.get("next_actions", []),
            "question_by_question_feedback": final.get("question_by_question_feedback", []),
            # 레거시 호환 키
            "assessment_of_plan_achievement": final.get("interview_flow_rationale", ""),
            "core_competency_analysis": [],
            "growth_potential": "",
        }

    # ----------------------------- 레거시(옵션) -----------------------------
    def generate_final_report(self, transcript: List[Dict], interview_plan: Dict, resume_feedback_analysis: Dict) -> Dict:
        """레거시 단일 패스 리포트(유지). 긴 요약/팔로업 포함 + 클린업."""
        print("\n\n" + "#" * 70)
        print(f" 최종 역량 분석 종합 리포트(레거시) 생성 중... (면접관: {self.interviewer_mode})")
        print("#" * 70)
        try:
            conversation_summary = ""
            for item in transcript:
                q_id = item.get("question_id", "N/A")
                rag_analysis = (item.get("analysis") or {}).get("rag_analysis", {})
                analysis_line = rag_analysis.get("analysis", "")
                if not analysis_line:
                    structured_analysis = (item.get("analysis") or {}).get("structured", {})
                    scoring_info = structured_analysis.get("scoring", {})
                    analysis_line = scoring_info.get("scoring_reason", "")

                conversation_summary += (
                    f"질문 {q_id} ({item.get('stage', 'N/A')}): {item.get('question', '')}\n"
                    f"답변 {q_id}: {_truncate(item.get('answer', ''), 400)}\n"
                    f"(개별 분석 요약: {_truncate(analysis_line or '분석 요약 없음', 600)})\n"
                )
                if item.get("follow_up_question"):
                    conversation_summary += (
                        f"(꼬리) 질문 {q_id}: {item['follow_up_question']}\n"
                        f"(꼬리) 답변 {q_id}: {_truncate(item.get('follow_up_answer',''), 300)}\n"
                    )
                conversation_summary += "---\n"

            persona_desc = self.persona["persona_description"].replace("{company_name}", self.company_name).replace("{job_title}", self.job_title)
            report_prompt = (
                prompt_rag_final_report
                .replace("{persona_description}", persona_desc)
                .replace("{final_report_goal}", self.persona["final_report_goal"])
                .replace("{company_name}", self.company_name)
                .replace("{job_title}", self.job_title)
                .replace("{conversation_summary}", _truncate(conversation_summary, 5500))
                .replace("{interview_plan}", _truncate(json.dumps(interview_plan, ensure_ascii=False), 4200))
                .replace("{resume_feedback_analysis}", _truncate(json.dumps(resume_feedback_analysis, ensure_ascii=False), 4200))
            )

            raw = self._chat_json(report_prompt, temperature=0.3, max_tokens=4000)
            report_data = safe_extract_json(raw) or {}
            report_data = self._cleanup_assessments(report_data)
            return report_data

        except Exception as e:
            print(f"❌ 최종 리포트(레거시) 생성 중 오류: {e}")
            traceback.print_exc()
            return {"error": f"final_report_failed: {e}"}

    def print_final_report(self, report: Dict):
        if not report:
            return

        print("\n\n" + "=" * 70)
        print(f"🏅 {self.company_name} {self.job_title} 지원자 최종 역량 분석 종합 리포트 (관점: {self.interviewer_mode})")
        print("=" * 70)

        print("\n■ 면접 계획 달성도/흐름 근거\n" + "-" * 50)
        print(report.get("assessment_of_plan_achievement", report.get("interview_flow_rationale", "평가 정보 없음.")))

        print("\n■ 총평 (Overall Summary)\n" + "-" * 50)
        print(report.get("overall_summary", "요약 정보 없음."))

        strengths = report.get("strengths_matrix", [])
        weaknesses = report.get("weaknesses_matrix", [])
        if strengths:
            print("\n■ 강점 매트릭스\n" + "-" * 50)
            for s in strengths:
                print(f"  - {s.get('theme','N/A')} :: evidence={s.get('evidence',[])}")
        if weaknesses:
            print("\n■ 약점 매트릭스\n" + "-" * 50)
            for w in weaknesses:
                print(f"  - {w.get('theme','N/A')} (sev={w.get('severity','N/A')}) :: evidence={w.get('evidence',[])}")

        agg = report.get("score_aggregation", {})
        if agg:
            print("\n■ 점수 집계/캘리브레이션\n" + "-" * 50)
            print(json.dumps(agg, ensure_ascii=False))

        if "resume_feedback" in report:
            print("\n■ 이력서 피드백 (Resume Feedback)\n" + "-" * 50)
            feedback = report.get("resume_feedback", {})
            if isinstance(feedback, dict):
                print(f"  - 직무 적합성: {feedback.get('job_fit_assessment', 'N/A')}")
                print(f"  - 강점 및 기회: {feedback.get('strengths_and_opportunities', 'N/A')}")
                print(f"  - 개선점: {feedback.get('gaps_and_improvements', 'N/A')}")
            else:
                print(f"  {feedback}")

        if "question_by_question_feedback" in report:
            print("\n■ 질문별 상세 피드백 (Question-by-Question Feedback)\n" + "-" * 50)
            for item in report.get("question_by_question_feedback", []):
                print(f"  - 질문ID: {item.get('question_id','-')} / 질문: {item.get('question', 'N/A')}")
                if item.get("stage"): print(f"    - 단계: {item.get('stage')}")
                if item.get("objective"): print(f"    - 목표: {item.get('objective')}")
                print(f"    - 질문 의도: {item.get('question_intent', 'N/A')}")
                evaluation = item.get("evaluation", {})
                if isinstance(evaluation, dict):
                    print(f"    - 적용 프레임워크: {evaluation.get('applied_framework', 'N/A')}")
                    if evaluation.get("scores_main"): print(f"    - Main: {evaluation.get('scores_main')}")
                    if evaluation.get("scores_ext"): print(f"    - Ext : {evaluation.get('scores_ext')}")
                    print(f"    - 피드백: {evaluation.get('feedback', 'N/A')}")
                else:
                    print(f"    - 피드백: {evaluation}")
                if item.get("model_answer"):
                    print("    - 모범답변: " + _truncate(item.get("model_answer",""), 600))
                if item.get("additional_followups"):
                    print(f"    - 추가 꼬리질문: {item.get('additional_followups')}")
                print("    " + "-" * 20)

        print("\n" + "=" * 70)


# ============================== CLI 진입점 ==============================
def main():
    try:
        target_container = "interview-data"
        company_name = input("면접을 진행할 회사 이름 (예: 기아): ")
        safe_company_name_for_index = unidecode((company_name or '').lower()).replace(" ", "-") or "unknown"
        index_name = f"{safe_company_name_for_index}-report-index"
        job_title = input("지원 직무 (예: 생산 - 생산운영 및 공정기술): ")
        difficulty = input("면접 난이도 (easy, normal, hard): ") or "normal"
        interviewer_mode = input("면접관 모드 (team_lead, executive): ") or "team_lead"

        print("\n" + "-" * 40)
        print(f"대상 컨테이너: {target_container}")
        print(f"회사 이름: {company_name}")
        print(f"AI Search 인덱스: {index_name}")
        print(f"난이도: {difficulty}")
        print(f"면접관 모드: {interviewer_mode}")
        print("-" * 40)

        bot = RAGInterviewBot(
            company_name=company_name,
            job_title=job_title,
            container_name=target_container,
            index_name=index_name,
            difficulty=difficulty,
            interviewer_mode=interviewer_mode,
            sync_on_init=False,  # 기본값: 초기 동기화 비활성
        )
        bot.conduct_interview()

    except Exception as e:
        print(f"\n❌ 시스템 실행 중 심각한 오류 발생: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
