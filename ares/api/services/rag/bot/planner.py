
"""
Interview Planner module for the RAG Interview Bot.

This module is responsible for designing the structured interview plan.
"""

from typing import Any, Dict, List, Optional

from ares.api.services.prompts import (
    DIFFICULTY_INSTRUCTIONS,
    prompt_interview_designer,
    prompt_rag_json_correction,
    prompt_icebreaker_question,
    prompt_self_introduction_question,
    prompt_motivation_question,
)
from ares.api.utils.ai_utils import safe_extract_json

from .base import RAGBotBase
from .utils import (
    _truncate,
    _debug_print_raw_json,
    _force_json_like,
    _normalize_plan_local,
)

class InterviewPlanner(RAGBotBase):
    """Designs the interview plan using RAG and LLM prompts."""

    def design_interview_plan(self) -> Dict:
        if not self.rag_ready:
            return {"error": "RAG system is not ready.", "interview_plan": []}

        print(f"\n🧠 Designing custom interview plan for {self.company_name} (Difficulty: {self.difficulty}, Interviewer: {self.interviewer_mode})...")
        try:
            business_info = self._get_company_business_info()

            ncs_info = ""
            ncs_dict = self._ensure_ncs_dict(self.ncs_context)
            if isinstance(ncs_dict.get("ncs"), list):
                ncs_titles = [it.get("title") for it in ncs_dict["ncs"] if isinstance(it, dict) and it.get("title")]
                if ncs_titles:
                    ncs_info = f"\n\nNCS Job Information: {', '.join(ncs_titles[:6])}."

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

            initial_stages = [
                {
                    "stage": "아이스브레이킹",
                    "objective": "Ice-breaking to ease tension.",
                    "questions": [self._chat_text(prompt_icebreaker_question, temperature=0.7, max_tokens=100)]
                },
                {
                    "stage": "자기소개",
                    "objective": "To understand the candidate's background and core competencies.",
                    "questions": [self._chat_text(prompt_self_introduction_question, temperature=0.7, max_tokens=100)]
                },
                {
                    "stage": "지원 동기",
                    "objective": "To verify interest and understanding of the company and role.",
                    "questions": [self._chat_text(prompt_motivation_question, temperature=0.7, max_tokens=100)]
                },
            ]
            normalized = initial_stages + (normalized or [])

            if not normalized or all(not st.get("questions") for st in normalized):
                _debug_print_raw_json("PLAN_FIRST_PASS", raw or "")
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
                normalized2 = _normalize_plan_local(corrected)
                if normalized2:
                    normalized = initial_stages + normalized2
                else:
                    _debug_print_raw_json("PLAN_CORRECTION_FAILED", correction_raw)

            if not normalized:
                single = self.generate_opening_question(
                    company_name=self.company_name,
                    job_title=self.job_title,
                    difficulty=self.difficulty,
                    context_hint={"business_info": business_info},
                )
                normalized = [{
                    "stage": "Opening",
                    "objective": "To verify candidate's basic competency and thinking process.",
                    "questions": [single] if single else []
                }]

            print("✅ Structured interview plan designed successfully." if any(st.get("questions") for st in normalized) else "⚠️ Interview plan is empty.")
            return {"interview_plan": normalized}

        except Exception as e:
            error_msg = f"Failed to design interview plan: {e}"
            print(f"❌ {error_msg}")
            return {
                "error": error_msg,
                "interview_plan": [],
            }

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
        ncs_dict = self._ensure_ncs_dict(self.ncs_context)
        ncs_titles = [it.get("title") for it in ncs_dict.get("ncs", []) if isinstance(it, dict) and it.get("title")]
        if ncs_titles:
            hints.append("NCS: " + ", ".join(ncs_titles[:5]))

        prompt = (
            f"[Role] You are an interviewer for {company_name} for the {job_title} position, with the persona of {self.interviewer_mode}.\n"
            f"[Difficulty] {difficulty}\n"
            "[Request] Output a single opening question to verify the candidate's competency. Induce them to provide metrics, evidence, or examples.\n"
            f"[Hints]\n- " + ("\n- ".join(hints) if hints else "(None)")
        )
        try:
            text = self._chat_text(prompt, temperature=0.4, max_tokens=200)
            return text.strip().split("\n")[0].strip()
        except Exception as e:
            print(f"❌ Failed to generate a single opening question: {e}")
            return ""

