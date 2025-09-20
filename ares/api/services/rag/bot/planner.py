# ares/api/services/rag/bot/planner.py
"""
Interview Planner module for the RAG Interview Bot.

- 회사/직무 RAG 요약 + 이력/JD/연구/NCS 컨텍스트를 기반으로
  면접 계획(interview_plan)을 설계합니다.
- 출력은 {"interview_plan": [...]} 형태로 반환합니다.
"""

from typing import Any, Dict, List, Optional
import logging

from ares.api.services.prompts import (
    DIFFICULTY_INSTRUCTIONS,
    prompt_interview_designer_v2,
    make_icebreak_question_llm_or_template,
)
from .base import RAGBotBase
from .utils import (
    _truncate,
    normalize_llm_json,
    safe_get_any,
)

logger = logging.getLogger(__name__)

class InterviewPlanner:
    """Designs the interview plan using RAG and LLM prompts."""
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def design_interview_plan(self) -> Dict:
        """
        LLM으로 인터뷰 계획을 설계하고, V2 스키마를 내부 표준 스키마로 변환하여 반환합니다.
        """
        if not self.bot.rag_ready:
            return {"error": "RAG system is not ready.", "interview_plan": []}

        print(f"\n🧠 Designing custom interview plan for {self.bot.company_name} (Difficulty: {self.bot.difficulty}, Interviewer: {self.bot.interviewer_mode})...")
        try:
            business_info = self.bot._get_company_business_info()
            ncs_info = ""
            ncs_dict = self.bot._ensure_ncs_dict(self.bot.ncs_context)
            if isinstance(ncs_dict.get("ncs"), list):
                ncs_titles = [it.get("title") for it in ncs_dict["ncs"] if isinstance(it, dict) and it.get("title")]
                if ncs_titles:
                    ncs_info = f"\n\nNCS Job Information: {', '.join(ncs_titles[:6])}."

            persona_description = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(self.bot.difficulty, "")

            prompt = (
                prompt_interview_designer_v2
                .replace("{persona_description}", persona_description)
                .replace("{question_style_guide}", self.bot.persona["question_style_guide"])
                .replace("{company_name}", self.bot.company_name)
                .replace("{job_title}", self.bot.job_title)
                .replace("{difficulty_instruction}", difficulty_instruction)
                .replace("{business_info}", business_info)
                .replace("{jd_context}", _truncate(self.bot.jd_context, 1200))
                .replace("{resume_context}", _truncate(self.bot.resume_context, 1200))
                .replace("{research_context}", _truncate(self.bot.research_context, 1200))
                .replace("{ncs_info}", _truncate(ncs_info, 400))
            )

            raw = self.bot._chat_json(prompt, temperature=0.3, max_tokens=3200)
            normalized_data = normalize_llm_json(raw)

            # V2 응답("phases")을 내부 표준 형식("stages")으로 변환
            v2_phases = []
            if isinstance(normalized_data, dict):
                v2_phases = normalized_data.get("phases", [])
            elif isinstance(normalized_data, list):
                # LLM이 최상위 리스트를 바로 반환하는 경우도 처리
                v2_phases = normalized_data

            if not isinstance(v2_phases, list):
                v2_phases = []

            # `normalize_interview_plan` 함수가 이해할 수 있도록 `phase` -> `stage` 키 변경
            transformed_stages = []
            for phase in v2_phases:
                if not isinstance(phase, dict): continue
                new_stage = phase.copy()
                if 'phase' in new_stage:
                    new_stage['stage'] = new_stage.pop('phase')
                transformed_stages.append(new_stage)

            final_plan = {"interview_plan": transformed_stages}

            # ----- 아이스브레이킹 질문 추가 (V2 계획에 icebreaker가 없을 경우 대비) -----
            try:
                # V2 계획에 이미 아이스브레이커가 있는지 확인
                has_icebreaker_in_plan = False
                for stage in final_plan.get("interview_plan", []):
                    if stage.get("stage") == "intro":
                        for item in stage.get("items", []):
                            if item.get("question_type") == "icebreaking":
                                has_icebreaker_in_plan = True
                                break
                    if has_icebreaker_in_plan: break
                
                if not has_icebreaker_in_plan:
                    icebreaker_text = make_icebreak_question_llm_or_template(llm_call=self.bot._chat)
                    icebreaker_question = {
                        "id": "icebreaker-1",
                        "type": "icebreaking",
                        "question": icebreaker_text,
                        "followups": []
                    }
                    # 별도 키에 저장 (기존 로직 호환)
                    final_plan["icebreakers"] = [icebreaker_question]

            except Exception as ice_e:
                logger.warning(f"Could not generate icebreaker question: {ice_e}")
                final_plan["icebreakers"] = []

            print("✅ Structured interview plan designed successfully.")
            return final_plan

        except Exception as e:
            error_msg = f"Failed to design interview plan: {e}"
            print(f"❌ {error_msg}")
            return {"error": error_msg, "interview_plan": []}
