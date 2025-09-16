# ares/api/services/rag/final_interview_rag.py
import json
import re
import traceback
from typing import Any

from openai import AzureOpenAI
from unidecode import unidecode
from django.conf import settings

# RAG 시스템
from .new_azure_rag_llamaindex import AzureBlobRAGSystem
# 웹 검색 도구
from .tool_code import google_search
# 프롬프트
from ares.api.services.prompt import (
    INTERVIEWER_PERSONAS,
    prompt_interview_designer,
    DIFFICULTY_INSTRUCTIONS,
    prompt_resume_analyzer,
    prompt_rag_answer_analysis,
    prompt_rag_json_correction,
    prompt_rag_follow_up_question,
    prompt_rag_final_report,
)
from ares.api.utils.ai_utils import safe_extract_json

def _escape_special_chars(text: str) -> str:
    """Azure AI Search/Lucene 예약문자 이스케이프"""
    # Lucene 예약 문자: + - && || ! ( ) { } [ ] ^ " ~ * ? : \
    pattern = r'([+\-&|!(){}\[\]^"~*?:\\])'
    return re.sub(pattern, r'\\\1', text or "")



# [PATCH] final_interview_rag.py 내부에 추가
import unicodedata

def _natural_num(s: str) -> int:
    try:
        # '1단계', '2단계' 같은 접미 텍스트 제거하고 숫자만
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else 10**6
    except Exception:
        return 10**6

def _extract_from_korean_schema(plan_data: Any) -> list[dict]:
    """
    다음과 같은 한글 스키마를 표준 스키마로 변환:
    {
      "면접 계획": {
        "1단계": { "목표": "...", "질문": [ {"질문": "..."}, {"질문": "..."} ] },
        "2단계": { ... },
        ...
      }
    }
    또는 "면접 계획" 없이 바로 {"1단계": {...}} 형태도 지원.
    """
    if not isinstance(plan_data, (dict, list)):
        return []

    root = plan_data
    if isinstance(root, dict) and "면접 계획" in root and isinstance(root["면접 계획"], dict):
        stages_dict = root["면접 계획"]
    elif isinstance(root, dict) and any(k.endswith("단계") for k in root.keys()):
        stages_dict = root
    else:
        return []

    norm: list[dict] = []
    # 단계 키를 자연스러운 순서로 정렬: 1단계, 2단계, ...
    for stage_key in sorted(stages_dict.keys(), key=_natural_num):
        stage_block = stages_dict.get(stage_key, {})
        if not isinstance(stage_block, dict):
            continue
        objective = (stage_block.get("목표") or stage_block.get("목 적") or "").strip() or None
        qs_raw = stage_block.get("질문") or []
        qs_list: list[str] = []
        if isinstance(qs_raw, list):
            for item in qs_raw:
                if isinstance(item, str):
                    qs_list.append(item.strip())
                elif isinstance(item, dict):
                    q = item.get("질문") or item.get("question") or item.get("Q")
                    if isinstance(q, str) and q.strip():
                        qs_list.append(q.strip())
        elif isinstance(qs_raw, dict):
            q = qs_raw.get("질문") or qs_raw.get("question")
            if isinstance(q, str) and q.strip():
                qs_list.append(q.strip())

        # 문장 과다 시 첫 문장만 보정
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
    """
    마크다운/설명문 섞인 응답에서 가장 바깥쪽 JSON 블록을 강제로 추출.
    """
    if not raw:
        return None
    # 코드펜스 제거
    raw2 = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)
    # 첫 { ... } 또는 [ ... ] 블록 시도
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

# [PATCH] 기존 _normalize_plan_local 함수 전체를 아래로 교체
def _normalize_plan_local(plan_data: Any) -> list[dict]:
    """
    다양한 변형 스키마를 표준 list[{stage, objective?, questions:[...]}] 로 정규화.
    - 영문: plan / interview_plan / questions / question / items
    - 국문: 면접 계획 / N단계 / 목표 / 질문[{질문:"..."}]
    """
    if not plan_data:
        return []

    # str -> JSON 시도 + 강제 JSON 블록 추출
    if isinstance(plan_data, str):
        plan_data = safe_extract_json(plan_data, default=None) or _force_json_like(plan_data) or {}

    # 1) 한국어 스키마 먼저 시도
    ko_norm = _extract_from_korean_schema(plan_data)
    if ko_norm:
        return ko_norm

    # 2) 영문/일반 스키마
    candidate = (
        plan_data.get("plan")
        if isinstance(plan_data, dict) and "plan" in plan_data
        else plan_data.get("interview_plan")
        if isinstance(plan_data, dict) and "interview_plan" in plan_data
        else plan_data
    )

    if isinstance(candidate, dict):
        # 단일 스테이지 or dict 모음
        if "stage" in candidate and any(k in candidate for k in ("questions", "question", "items")):
            candidate = [candidate]
        else:
            candidate = [v for v in candidate.values() if isinstance(v, dict)]

    if not isinstance(candidate, list):
        return []

    norm: list[dict] = []
    for i, st in enumerate(candidate):
        if not isinstance(st, dict):
            continue
        stage = st.get("stage") or f"Stage {i+1}"
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


# -----------------------------
# Bot
# -----------------------------
class RAGInterviewBot:
    """RAG + LLM 기반 구조화 면접 Bot (하드닝 버전)"""

    def __init__(
        self,
        company_name: str,
        job_title: str,
        container_name: str,
        index_name: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",
        ncs_context: dict | None = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        **kwargs,
    ):
        print(f"🤖 RAG 전용 사업 분석 면접 시스템 초기화 (면접관: {interviewer_mode})...")
        self.company_name = company_name
        self.job_title = job_title
        self.difficulty = difficulty
        self.interviewer_mode = interviewer_mode
        self.ncs_context = ncs_context or {}
        self.jd_context = jd_context
        self.resume_context = resume_context
        self.research_context = research_context

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
        try:
            self.rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            blobs = list(self.rag_system.container_client.list_blobs())
            if not blobs:
                print(f"⚠️ 경고: Azure Blob 컨테이너 '{container_name}'에 분석할 파일이 없습니다.")
                return

            print(f"✅ Azure RAG 시스템 준비 완료. {len(blobs)}개의 문서를 기반으로 합니다.")
            print("🔄 Azure AI Search 인덱스 자동 동기화 시작...")
            self.rag_system.sync_index(company_name_filter=self.company_name)
            self.rag_ready = True

        except Exception as e:
            print(f"❌ RAG 시스템 연동 실패: {e}")

    # -----------------------------
    # 내부 LLM 호출 래퍼
    # -----------------------------
    def chat_plain(self, prompt: str) -> str:
        """
        JSON 스키마 강제 없이 '평문 1~2문장'을 받아올 때 사용.
        """
        res = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=300,
        )
        return (res.choices[0].message.content or "").strip()

    # -----------------------------
    # 플랜 생성
    # -----------------------------
    def design_interview_plan(self) -> dict:
        """
        RAG 기반 구조화 면접 계획 생성.
        1차: 표준 프롬프트 → JSON 파싱 → 정규화
        2차: JSON 파싱 실패 시 자가 보정 or 강제 JSON 추출
        3차: 여전히 비면 '단건 오프닝 질문'으로 최소 플랜 구성
        """
        if not self.rag_ready:
            return {}

        print(f"\n🧠 {self.company_name} 맞춤 면접 계획 설계 중 (난이도: {self.difficulty}, 면접관: {self.interviewer_mode})...")
        try:
            safe_company_name = _escape_special_chars(self.company_name)
            safe_job_title = _escape_special_chars(self.job_title)

            query_text = f"{safe_company_name}의 핵심 사업, 최근 실적, 주요 리스크, 그리고 {safe_job_title} 직무와 관련된 회사 정보에 대해 요약해줘."
            print(f"🔍 '{self.rag_system.index_name}' 인덱스에서 질문 처리: {query_text}")
            business_info = self.rag_system.query(query_text)

            # NCS 요약
            ncs_info = ""
            if self.ncs_context.get("ncs"):
                ncs_titles = [item.get("title") for item in self.ncs_context["ncs"] if item.get("title")]
                if ncs_titles:
                    ncs_info = f"\n\nNCS 직무 관련 정보: {', '.join(ncs_titles)}."

            difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(self.difficulty, "")
            prompt = prompt_interview_designer.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                question_style_guide=self.persona["question_style_guide"],
                company_name=self.company_name,
                job_title=self.job_title,
                difficulty_instruction=difficulty_instruction,
                business_info=business_info,
                jd_context=self.jd_context,
                resume_context=self.resume_context,
                research_context=self.research_context,
                ncs_info=ncs_info,
            )

            # 1차 시도
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.4,
            )
            raw = response.choices[0].message.content or ""
            parsed = safe_extract_json(raw) or _force_json_like(raw) or {}
            normalized = _normalize_plan_local(parsed)

            # 2차 시도: JSON 교정
            if not normalized:
                _debug_print_raw_json("PLAN_FIRST_PASS", raw)
                try:
                    correction = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": prompt_rag_json_correction}
                        ],
                        temperature=0.0,
                        max_tokens=2000,
                    )
                    corrected_raw = correction.choices[0].message.content or ""
                    corrected = safe_extract_json(corrected_raw) or _force_json_like(corrected_raw) or {}
                    normalized = _normalize_plan_local(corrected)
                    if not normalized:
                        _debug_print_raw_json("PLAN_CORRECTION_FAILED", corrected_raw)
                except Exception as e2:
                    print(f"⚠️ 플랜 JSON 교정 실패: {e2}")

            # 3차 시도: 단건 오프닝으로 최소 플랜 구성
            if not normalized:
                print("ℹ️ 플랜 정규화 결과가 비어 단건 오프닝 질문으로 최소 플랜 구성.")
                single = self.generate_opening_question(
                    company_name=self.company_name,
                    job_title=self.job_title,
                    difficulty=self.difficulty,
                    context_hint={"business_info": business_info},
                )
                if single:
                    normalized = [{
                        "stage": "Opening",
                        "objective": "지원자의 생산운영/공정기술 기본 역량과 사고방식 검증",
                        "questions": [single],
                    }]

            print("✅ 구조화 면접 계획 수립 완료." if normalized else "⚠️ 구조화 면접 계획이 비어있음.")
            return {"interview_plan": normalized}

        except Exception as e:
            print(f"❌ 면접 계획 수립 실패: {e}")
            traceback.print_exc()
            return {}

    # -----------------------------
    # 오프닝 단건 질문 생성기 (폴백)
    # -----------------------------
    def generate_opening_question(
        self,
        company_name: str,
        job_title: str,
        difficulty: str,
        context_hint: dict | None = None,
    ) -> str:
        """
        플랜이 비거나 질문 추출 실패 시 사용. 평문 1문장.
        """
        hints = []
        if isinstance(context_hint, dict):
            bi = context_hint.get("business_info")
            if bi:
                # 앞부분만 힌트로 사용
                hints.append(str(bi)[:600])
        ncs_titles = [it.get("title") for it in (self.ncs_context or {}).get("ncs", []) if it.get("title")]
        if ncs_titles:
            hints.append("NCS: " + ", ".join(ncs_titles[:5]))

        prompt = (
            f"[역할] 당신은 {company_name} {job_title} 면접의 {self.interviewer_mode} 면접관\n"
            f"[난이도] {difficulty}\n"
            "[요청] 지원자의 생산운영/공정기술 역량을 검증할 '오프닝 질문' 1문장만 출력.\n"
            "모호한 표현을 피하고, 수치/근거/사례 제시를 유도할 것.\n"
            f"[힌트]\n- " + ("\n- ".join(hints) if hints else "(없음)")
        )
        try:
            text = self.chat_plain(prompt)
            # 문장 끝 보정
            text = text.strip().split("\n")[0].strip()
            return text
        except Exception as e:
            print(f"❌ 단건 오프닝 질문 생성 실패: {e}")
            return ""

    # -----------------------------
    # 이력서/RAG 분석
    # -----------------------------
    def analyze_resume_with_rag(self) -> dict:
        if not self.rag_ready or not self.resume_context:
            return {}
        print(f"\n📄 RAG 기반 이력서 분석 중 (면접관: {self.interviewer_mode})...")
        try:
            safe_company_name = _escape_special_chars(self.company_name)
            safe_job_title = _escape_special_chars(self.job_title)
            business_info = self.rag_system.query(
                f"{safe_company_name}의 핵심 사업, 최근 실적, 주요 리스크, 그리고 {safe_job_title} 직무와 관련된 회사 정보에 대해 요약해줘."
            )

            prompt = prompt_resume_analyzer.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                company_name=self.company_name,
                job_title=self.job_title,
                business_info=business_info,
                resume_context=self.resume_context,
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.3,
            )
            result = safe_extract_json(response.choices[0].message.content)
            print("✅ 이력서-회사 연관성 분석 완료.")
            return result
        except Exception as e:
            print(f"❌ 이력서 분석 실패: {e}")
            return {}

    def analyze_answer_with_rag(self, question: str, answer: str) -> dict:
        if not self.rag_ready:
            return {"error": "RAG 시스템 미준비"}

        print(f"    (답변 분석 중... 면접관: {self.interviewer_mode})")

        try:
            web_result = google_search.search(queries=[f"{self.company_name} {answer}"])
            if not isinstance(web_result, str):
                web_result = json.dumps(web_result, ensure_ascii=False)[:2000]
        except Exception:
            web_result = "검색 실패 또는 결과 없음"

        safe_answer = _escape_special_chars(answer)
        internal_check = self.rag_system.query(
            f"'{safe_answer}'라는 주장에 대한 사실관계를 확인하고 관련 데이터를 찾아줘."
        )

        analysis_prompt = prompt_rag_answer_analysis.format(
            persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
            evaluation_focus=self.persona["evaluation_focus"],
            company_name=self.company_name,
            question=question,
            answer=answer,
            internal_check=internal_check,
            web_result=web_result,
        )

        raw_json = ""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Respond with ONLY a JSON object that strictly matches the intended structure. No prose, no code fences."},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.2,
                max_tokens=2000,
            )
            raw_json = response.choices[0].message.content or ""
            result = safe_extract_json(raw_json)
            if result is not None:
                return result
            raise json.JSONDecodeError("Initial JSON parsing failed, attempting self-correction", raw_json, 0)

        except json.JSONDecodeError as e:
            _debug_print_raw_json("FIRST_PASS_FAILED", raw_json)
            print(f"⚠️ JSON 파싱 실패 ({e}), AI 자가 교정 시도.")
            try:
                correction_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                        {"role": "user", "content": analysis_prompt},
                        {"role": "assistant", "content": raw_json},
                        {"role": "user", "content": prompt_rag_json_correction}
                    ],
                    temperature=0.0,
                    max_tokens=2000,
                )
                corrected_raw = correction_response.choices[0].message.content or ""
                final_result = safe_extract_json(corrected_raw)
                if final_result is not None:
                    return final_result
                _debug_print_raw_json("CORRECTION_PASS_FAILED", corrected_raw)
                raise json.JSONDecodeError("Failed to parse AI response after self-correction", corrected_raw, 0)
            except Exception as e_corr:
                print(f"❌ 답변 분석 최종 실패: {e_corr}")
                traceback.print_exc()
                return {"error": f"Failed to parse AI response: {e_corr}"}
        except Exception as e_gen:
            print(f"❌ 답변 분석 실패 (일반 오류): {e_gen}")
            traceback.print_exc()
            return {"error": f"Failed to analyze answer: {e_gen}"}

    # -----------------------------
    # 리포트/출력 (CLI)
    # -----------------------------
    def print_individual_analysis(self, analysis: dict, question_num: str):
        if "error" in analysis:
            print(f"\n❌ 분석 오류: {analysis['error']}")
            return

        print("\n" + "=" * 70)
        print(f"📊 [{question_num}] 답변 상세 분석 결과")
        print("=" * 70)

        print("\n" + "-" * 30)
        print("✅ 주장별 사실 확인 (Fact-Checking)")
        checks = analysis.get("claims_checked", []) or analysis.get("fact_checking", [])
        if not checks:
            print("  - 확인된 주장이 없습니다.")
        else:
            for c in checks:
                claim = c.get("claim", "N/A")
                verdict = c.get("verdict") or c.get("verification") or "N/A"
                src = c.get("evidence_source", "")
                rationale = c.get("rationale") or c.get("evidence") or "N/A"
                print(f'  - 주장: "{claim}"')
                print(f'    - 판정: {verdict} {f"({src})" if src else ""}')
                print(f'    - 근거: {rationale}')

        print("\n" + "-" * 30)
        print("📝 내용 분석 (Content Analysis)")
        summary = analysis.get("analysis", "")
        if not summary:
            ca = analysis.get("content_analysis", {})
            if isinstance(ca, dict):
                depth = ca.get("analytical_depth", {})
                insight = ca.get("strategic_insight", {})
                parts = []
                if isinstance(depth, dict):
                    parts.append(f"[분석 깊이] {depth.get('assessment','N/A')}: {depth.get('comment','')}")
                if isinstance(insight, dict):
                    parts.append(f"[통찰] {insight.get('assessment','N/A')}: {insight.get('comment','')}")
                summary = " / ".join([p for p in parts if p])
        print(f"  - 요약: {summary or 'N/A'}")

        print("\n" + "-" * 30)
        print("💡 실행 가능한 피드백 (Actionable Feedback)")
        fb = analysis.get("feedback", "")
        if fb:
            print(f"  - {fb}")
        else:
            af = analysis.get("actionable_feedback", {})
            strengths = af.get("strengths", []) if isinstance(af, dict) else []
            sugg = af.get("suggestions_for_improvement", []) if isinstance(af, dict) else []
            if strengths:
                print("  - 강점:")
                for s in strengths:
                    print(f"    ✓ {s}")
            if sugg:
                print("  - 개선 제안:")
                for s in sugg:
                    print(f"    -> {s}")
            if not strengths and not sugg:
                print("  - 피드백 없음")
        print("=" * 70)

    def generate_follow_up_question(self, original_question: str, answer: str, analysis: dict, stage: str, objective: str) -> str:
        try:
            suggestions_str = ""
            if isinstance(analysis, dict):
                if analysis.get("feedback"):
                    suggestions_str = analysis["feedback"]
                else:
                    af = analysis.get("actionable_feedback", {})
                    hints = []
                    if isinstance(af, dict):
                        hints += af.get("suggestions_for_improvement", [])
                        hints += af.get("strengths", [])
                    suggestions_str = ", ".join(hints[:5])

            prompt = prompt_rag_follow_up_question.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                company_name=self.company_name,
                original_question=original_question,
                answer=answer,
                suggestions=suggestions_str,
                stage=stage,
                objective=objective,
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            result = safe_extract_json(response.choices[0].message.content)
            return (result or {}).get("follow_up_question", "")
        except Exception as e:
            print(f"❌ 꼬리 질문 생성 실패: {e}")
            return ""

    # -----------------------------
    # CLI 인터뷰 시나리오
    # -----------------------------
    def conduct_interview(self):
        if not self.rag_ready:
            print("\n❌ RAG 시스템이 준비되지 않아 면접을 진행할 수 없습니다.")
            return

        resume_analysis = self.analyze_resume_with_rag()
        interview_plan_data = self.design_interview_plan()

        plan = (
            interview_plan_data.get("plan")
            or interview_plan_data.get("interview_plan")
            or (interview_plan_data if isinstance(interview_plan_data, list) else None)
        )
        if not plan:
            print("\n❌ 면접 계획을 수립하지 못했습니다.")
            return

        interview_plan = plan

        print("\n" + "=" * 70)
        print(f"🏢 {self.company_name} {self.job_title} 직무 {self.interviewer_mode} 면접을 시작하겠습니다.")
        print("면접은 총 3단계로 구성되며, 각 단계의 질문에 답변해주시면 됩니다.")
        print("면접이 종료된 후 전체 답변에 대한 상세 분석이 제공됩니다.")
        print("=" * 70)

        interview_transcript = []
        interview_stopped = False

        for i, stage_data in enumerate(interview_plan, 1):
            stage_name = stage_data.get("stage", f"단계 {i}")
            objectives = stage_data.get("objectives") or stage_data.get("objective")
            if isinstance(objectives, list):
                stage_objective = objectives[0] if objectives else "N/A"
            else:
                stage_objective = objectives or "N/A"
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

                analysis = self.analyze_answer_with_rag(question, answer)
                follow_up_question = ""
                follow_up_answer = ""
                if "error" not in analysis:
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

    def _generate_and_print_reports(self, transcript, plan_data, resume_analysis):
        print("\n\n" + "#" * 70)
        print(" 면접 전체 답변에 대한 상세 분석 리포트")
        print("#" * 70)

        for item in transcript:
            self.print_individual_analysis(item["analysis"], item["question_id"])

        report = self.generate_final_report(transcript, plan_data, resume_analysis)
        self.print_final_report(report)

    def generate_final_report(self, transcript: list, interview_plan: dict, resume_feedback_analysis: dict) -> dict:
        print("\n\n" + "#" * 70)
        print(f" 최종 역량 분석 종합 리포트 생성 중... (면접관: {self.interviewer_mode})")
        print("#" * 70)

        try:
            conversation_summary = ""
            for item in transcript:
                q_id = item.get("question_id", "N/A")
                analysis_line = ""
                if isinstance(item.get("analysis"), dict):
                    analysis_line = item["analysis"].get("analysis", "")
                    if not analysis_line:
                        ca = item["analysis"].get("content_analysis", {})
                        if isinstance(ca, dict):
                            si = ca.get("strategic_insight", {})
                            if isinstance(si, dict):
                                analysis_line = si.get("assessment", "") or si.get("comment", "")

                conversation_summary += (
                    f"질문 {q_id} ({item.get('stage', 'N/A')}): {item.get('question', '')}\n"
                    f"답변 {q_id}: {item.get('answer', '')[:200]}\n"
                    f"(개별 분석 요약: {analysis_line or '분석 요약 없음'})\n---\n"
                )

            report_prompt = prompt_rag_final_report.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                final_report_goal=self.persona["final_report_goal"],
                company_name=self.company_name,
                job_title=self.job_title,
                conversation_summary=conversation_summary[:4000],
                interview_plan=json.dumps(interview_plan, ensure_ascii=False)[:4000],
                resume_feedback_analysis=json.dumps(resume_feedback_analysis, ensure_ascii=False)[:4000],
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": report_prompt}],
                temperature=0.3,
                max_tokens=4000,
            )
            report_data = safe_extract_json(response.choices[0].message.content)
            return report_data

        except Exception as e:
            print(f"❌ 최종 리포트 생성 중 오류 발생: {e}")
            traceback.print_exc()
            return {}

    def print_final_report(self, report: dict):
        if not report:
            return

        print("\n\n" + "=" * 70)
        print(f"🏅 {self.company_name} {self.job_title} 지원자 최종 역량 분석 종합 리포트 (관점: {self.interviewer_mode})")
        print("=" * 70)

        print("\n■ 면접 계획 달성도 평가\n" + "-" * 50)
        print(report.get("assessment_of_plan_achievement", "평가 정보 없음."))

        print("\n■ 총평 (Overall Summary)\n" + "-" * 50)
        print(report.get("overall_summary", "요약 정보 없음."))

        print("\n■ 핵심 역량 분석 (Core Competency Analysis)\n" + "-" * 50)
        for comp in report.get("core_competency_analysis", []):
            print(f"  - {comp.get('competency', 'N/A')}: **{comp.get('assessment', 'N/A')}**")
            print(f"    - 근거: {comp.get('evidence', 'N/A')}")

        print("\n■ 성장 가능성 (Growth Potential)\n" + "-" * 50)
        print(f"  {report.get('growth_potential', 'N/A')}")

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
                print(f"  - 질문: {item.get('question', 'N/A')}")
                print(f"    - 질문 의도: {item.get('question_intent', 'N/A')}")
                evaluation = item.get("evaluation", {})
                if isinstance(evaluation, dict):
                    print(f"    - 적용된 프레임워크: {evaluation.get('applied_framework', 'N/A')}")
                    print(f"    - 피드백: {evaluation.get('feedback', 'N/A')}")
                else:
                    print(f"    - 피드백: {evaluation}")
                print("    " + "-" * 20)

        print("\n" + "=" * 70)


def main():
    try:
        target_container = "interview-data"
        company_name = input("면접을 진행할 회사 이름 (예: 기아): ")
        safe_company_name_for_index = unidecode(company_name.lower()).replace(" ", "-")
        index_name = f"{safe_company_name_for_index}-report-index"
        job_title = input("지원 직무 (예: 생산 - 생산운영 및 공정기술): ")
        difficulty = input("면접 난이도 (easy, normal, hard): ")
        interviewer_mode = input("면접관 모드 (team_lead, executive): ")

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
            interviewer_mode=interviewer_mode
        )
        bot.conduct_interview()

    except Exception as e:
        print(f"\n❌ 시스템 실행 중 심각한 오류 발생: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
