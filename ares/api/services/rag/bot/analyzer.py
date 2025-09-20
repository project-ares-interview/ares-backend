# ares/api/services/rag/bot/analyzer.py
"""
Answer Analyzer module for the RAG Interview Bot.

- 지원자 답변을 구조적으로 평가하고(Identifier → Extractor → Scorer → Explainer → Coach → ModelAnswer → BiasChecker),
- RAG + 웹 검색을 곁들인 서술형 분석을 수행,
- 꼬리질문(follow-ups)을 생성합니다.
"""

import json
import traceback
from typing import Any, Dict, List, Optional

from ares.api.services.prompts import (
    prompt_identifier,
    prompt_extractor,
    prompt_scorer,
    prompt_score_explainer,
    prompt_coach,
    prompt_bias_checker,
    prompt_model_answer,
    prompt_rag_answer_analysis,
    prompt_rag_json_correction,
    prompt_followup_v2,
)
from ares.api.utils.ai_utils import safe_extract_json
from ..tool_code import google_search

from .base import RAGBotBase
from .utils import _truncate, _escape_special_chars, _debug_print_raw_json

class AnswerAnalyzer:
    """Analyzes answers, generates follow-ups, and performs evaluations."""
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def analyze_answer_with_rag(self, question: str, answer: str, role: Optional[str] = None) -> Dict:
        role = role or self.bot.job_title
        print(f"(Analyzing answer... Interviewer: {self.bot.interviewer_mode})")
        structured = self._structured_evaluation(role=role, answer=answer)
        rag_analysis = self._rag_narrative_analysis(question=question, answer=answer)
        return {"structured": structured, "rag_analysis": rag_analysis}

    def _rag_narrative_analysis(self, question: str, answer: str) -> Dict:
        if not self.bot.rag_ready:
            return {"error": "RAG system not ready"}

        try:
            # 간단 웹 검색 (에러는 무시하되 분석 텍스트에 반영)
            try:
                web_result = google_search.search(queries=[f"{self.bot.company_name} {answer}"])
                if not isinstance(web_result, str):
                    web_result = _truncate(json.dumps(web_result, ensure_ascii=False), 2000)
            except Exception:
                web_result = "Search failed or no results"

            safe_answer = _escape_special_chars(answer)
            internal_check_raw = self.bot.rag_system.query(
                f"Fact-check the following claim and find related data: '{safe_answer}'"
            )
            internal_check = _truncate(internal_check_raw or "", 1200)

            persona_desc = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            analysis_prompt = (
                prompt_rag_answer_analysis
                .replace("{persona_description}", persona_desc)
                .replace("{evaluation_focus}", self.bot.persona["evaluation_focus"])
                .replace("{company_name}", self.bot.company_name)
                .replace("{question}", _truncate(question, 400))
                .replace("{answer}", _truncate(answer, 1500))
                .replace("{internal_check}", internal_check)
                .replace("{web_result}", _truncate(web_result, 1500))
            )

            raw_json = self.bot._chat_json(analysis_prompt, temperature=0.2, max_tokens=2000)
            result = safe_extract_json(raw_json)
            if result is not None:
                return result

            _debug_print_raw_json("RAG_FIRST_PASS", raw_json or "")
            corrected_raw = self.bot.client.chat.completions.create(
                model=self.bot.model,
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
            print(f"❌ RAG narrative analysis failed: {e}")
            traceback.print_exc()
            return {"error": f"Failed to analyze answer (RAG): {e}"}

    def _structured_evaluation(self, role: str, answer: str) -> Dict:
        """Identifier → Extractor → Scorer → ScoreExplainer → Coach → ModelAnswer → BiasChecker"""
        try:
            id_prompt = prompt_identifier.replace("{answer}", _truncate(answer, 1800))
            id_raw = self.bot._chat_json(id_prompt, temperature=0.1, max_tokens=800)
            id_json = safe_extract_json(id_raw) or {}
            frameworks: List[str] = id_json.get("frameworks", []) if isinstance(id_json, dict) else []
            values_summary = id_json.get("company_values_summary", "")

            base_fw = None
            for fw in frameworks:
                if isinstance(fw, str):
                    base_fw = (fw.split("+")[0] or "").upper().strip()
                    if base_fw:
                        break
            if not base_fw:
                base_fw = "STAR"

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
                + "\n[Candidate's Answer]\n"
                + _truncate(answer, 1800)
            )
            ex_raw = self.bot._chat_json(extractor_prompt, temperature=0.2, max_tokens=1600)
            ex_json = safe_extract_json(ex_raw) or {}

            ncs_titles = [item.get("title") for item in self.bot.ncs_context.get("ncs", []) if item.get("title")] if isinstance(self.bot.ncs_context.get("ncs"), list) else []
            ncs_details = _truncate(", ".join(ncs_titles), 1200)
            persona_desc_scorer = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            scorer_prompt = (
                prompt_scorer
                .replace("{framework_name}", base_fw)
                .replace("{retrieved_ncs_details}", ncs_details)
                .replace("{role}", role)
                .replace("{persona_description}", persona_desc_scorer)
                .replace("{evaluation_focus}", self.bot.persona["evaluation_focus"])
                + "\n[Candidate's Answer]\n"
                + _truncate(answer, 1800)
            )
            sc_raw = self.bot._chat_json(scorer_prompt, temperature=0.2, max_tokens=1500)
            sc_json = safe_extract_json(sc_raw) or {}

            persona_desc_explainer = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            expl_prompt = (
                prompt_score_explainer
                .replace("{framework}", json.dumps(sc_json.get("framework", base_fw), ensure_ascii=False))
                .replace("{scores_main}", json.dumps(sc_json.get("scores_main", {}), ensure_ascii=False))
                .replace("{scores_ext}", json.dumps(sc_json.get("scores_ext", {}), ensure_ascii=False))
                .replace("{scoring_reason}", _truncate(sc_json.get("scoring_reason", ""), 800))
                .replace("{role}", role)
                .replace("{persona_description}", persona_desc_explainer)
            )
            expl_raw = self.bot._chat_json(expl_prompt, temperature=0.2, max_tokens=2000)
            expl_json = safe_extract_json(expl_raw) or {}

            persona_desc_coach = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            coach_prompt = (
                prompt_coach
                .replace("{persona_description}", persona_desc_coach)
                .replace("{scoring_reason}", _truncate(sc_json.get("scoring_reason", ""), 800))
                .replace("{user_answer}", _truncate(answer, 1800))
                .replace("{retrieved_ncs_details}", ncs_details)
                .replace("{role}", role)
                .replace("{company_name}", self.bot.company_name)
            )
            coach_raw = self.bot._chat_json(coach_prompt, temperature=0.2, max_tokens=1400)
            coach_json = safe_extract_json(coach_raw) or {}

            persona_desc_model = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            model_prompt = (
                prompt_model_answer
                .replace("{persona_description}", persona_desc_model)
                .replace("{retrieved_ncs_details}", ncs_details)
            )
            model_raw = self.bot._chat_json(model_prompt, temperature=0.4, max_tokens=1400)
            model_json = safe_extract_json(model_raw) or {}

            def bias_sanitize(text: str) -> Dict:
                bprompt = prompt_bias_checker.replace("{any_text}", _truncate(text or "", 1600))
                braw = self.bot._chat_json(bprompt, temperature=0.0, max_tokens=1400)
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
            print(f"❌ Structured evaluation pipeline error: {e}")
            traceback.print_exc()
            return {"error": "structured_evaluation_failed: {e}"}

    def generate_follow_up_question(
        self,
        original_question: str,
        answer: str,
        analysis: Dict,
        stage: str,
        objective: str,
        *, 
        limit: int = 3,
        **kwargs,
    ) -> List[str]:
        """
        꼬리질문 생성.
        - 파라미터명은 limit로 통일(뷰에서도 limit 사용).
        - icebreaking/self_intro/motivation 단계에는 약간 다른 톤을 적용.
        """
        try:
            if "top_k" in kwargs and isinstance(kwargs["top_k"], int):
                limit = kwargs["top_k"]
            if "limit" in kwargs and isinstance(kwargs["limit"], int):
                limit = kwargs["limit"]

            phase_map = {
                "아이스브레이킹": "intro",
                "자기소개": "intro",
                "지원 동기": "intro",
            }
            question_type_map = {
                "아이스브레이킹": "icebreaking",
                "자기소개": "self_intro",
                "지원 동기": "motivation",
            }
            current_phase = phase_map.get(stage, "core")
            current_question_type = question_type_map.get(stage, "general")

            ncs_info = ""
            ncs_dict = self.bot._ensure_ncs_dict(self.bot.ncs_context)
            if isinstance(ncs_dict.get("ncs"), list):
                ncs_titles = [it.get("title") for it in ncs_dict["ncs"] if isinstance(it, dict) and it.get("title")]
                if ncs_titles:
                    ncs_info = f"NCS Job Information: {', '.join(ncs_titles[:6])}."

            persona_desc = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            
            # analysis 딕셔너리를 JSON 문자열로 변환하여 프롬프트에 전달
            analysis_summary = json.dumps(analysis, ensure_ascii=False, indent=2)

            prompt = (
                prompt_followup_v2
                .replace("{persona_description}", persona_desc)
                .replace("{phase}", current_phase)
                .replace("{question_type}", current_question_type)
                .replace("{objective}", objective or "")
                .replace("{latest_answer}", _truncate(answer, 1500))
                .replace("{analysis_summary}", _truncate(analysis_summary, 2000)) # 답변 분석 결과 추가
                .replace("{company_context}", self.bot.company_name)
                .replace("{ncs}", _truncate(ncs_info, 400))
                .replace("{kpi}", "[]")
            )

            raw = self.bot._chat_json(prompt, temperature=0.6, max_tokens=500)
            result = safe_extract_json(raw)

            if result and isinstance(result, dict):
                followups = result.get("followups", [])
                if isinstance(followups, list):
                    clean = [fu.strip() for fu in followups if isinstance(fu, str) and fu.strip()]
                    return clean[: max(1, int(limit))] if clean else []
            return []
        except Exception as e:
            print(f"❌ Follow-up question generation failed: {e}")
            traceback.print_exc()
            return []
