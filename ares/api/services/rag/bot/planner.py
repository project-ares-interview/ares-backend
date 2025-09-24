# ares/api/services/rag/bot/planner.py
"""
Interview Planner module for the RAG Interview Bot.

- 회사/직무 RAG 요약 + 이력/JD/연구/NCS 컨텍스트를 기반으로
  면접 계획(interview_plan)을 설계합니다.
- 출력은 {"interview_plan": [...]} 형태로 반환합니다.
"""
import json
from typing import Any, Dict, List, Optional
import logging

from ares.api.services.prompts import (
    DIFFICULTY_INSTRUCTIONS,
    prompt_extract_competencies,
    prompt_generate_question,
    prompt_create_rubric,
)
from ares.api.services.company_data import get_company_description
from .base import RAGBotBase
from .utils import (
    _truncate,
    normalize_llm_json,
    safe_get_any,
    _escape_special_chars,
    sanitize_plan_questions,
)

logger = logging.getLogger(__name__)

class InterviewPlanner:
    """Designs the interview plan using a Chain-of-Prompts (CoP) approach."""
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def _get_full_contexts(self) -> Dict[str, Any]:
        """모든 프롬프트에서 공통적으로 사용될 전체 컨텍스트를 준비합니다."""
        safe_company_name = _escape_special_chars(self.bot.company_name)
        safe_job_title = _escape_special_chars(self.bot.job_title)
        query_text = f"Summarize key business areas, recent performance, major risks for {safe_company_name}, especially related to the {safe_job_title} role."
        business_info = self.bot.summarize_company_context(query_text)

        ideal_candidate_profile = get_company_description(self.bot.company_name)
        if "정보 없음" in ideal_candidate_profile:
            ideal_candidate_profile = "(별도 인재상 정보 없음)"

        ncs_info = ""
        ncs_dict = self.bot._ensure_ncs_dict(self.bot.ncs_context)
        if isinstance(ncs_dict.get("ncs"), list):
            ncs_titles = [it.get("title") for it in ncs_dict["ncs"] if isinstance(it, dict) and it.get("title")]
            if ncs_titles:
                ncs_info = f"NCS Job Information: {', '.join(ncs_titles[:6])}."

        persona_description = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
        difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(self.bot.difficulty, "")

        return {
            "job_title": self.bot.job_title,
            "jd_context": _truncate(self.bot.jd_context, 8000),
            "resume_context": _truncate(self.bot.resume_context, 8000),
            "persona_description": persona_description,
            "business_info": business_info,
            "ideal_candidate_profile": ideal_candidate_profile,
            "research_context": _truncate(self.bot.research_context, 8000),
            "ncs_info": _truncate(ncs_info, 400),
            "difficulty_instruction": difficulty_instruction,
        }

    def _extract_competencies(self, contexts: Dict[str, Any]) -> List[str]:
        """1단계: JD와 이력서에서 검증할 핵심 역량을 추출합니다."""
        prompt = prompt_extract_competencies.format(**contexts)
        result = self.bot._chat_json(prompt, temperature=0.1, max_tokens=1024)
        normalized = normalize_llm_json(result)
        return normalized.get("competencies_to_verify", [])

    def _generate_question_for_competency(self, competency: str, contexts: Dict[str, Any]) -> Optional[Dict]:
        """2단계: 개별 역량에 대한 질문과 평가 포인트를 생성합니다."""
        prompt = prompt_generate_question.format(competency=competency, **contexts)
        result = self.bot._chat_json(prompt, temperature=0.3, max_tokens=1024)
        return normalize_llm_json(result)

    def _create_rubric_for_question(self, question_item: Dict) -> Optional[Dict]:
        """3단계: 생성된 질문에 대한 평가 기준(Rubric)을 생성합니다."""
        if not question_item or not question_item.get("question"):
            return None
        
        prompt = prompt_create_rubric.format(
            question=question_item["question"],
            expected_points=json.dumps(question_item.get("expected_points", []), ensure_ascii=False)
        )
        result = self.bot._chat_json(prompt, temperature=0.2, max_tokens=1024)
        return normalize_llm_json(result)

    def _assemble_full_plan(self, core_items: List[Dict]) -> Dict:
        """4단계: Intro, Core, Wrap-up 단계를 조합하여 최종 면접 계획을 완성합니다."""
        intro_phase = {
            "phase": "intro",
            "items": [
                {
                    "id": "intro-1",
                    "question_type": "icebreaking",
                    "question": "오늘 면접 장소까지 오시는 길은 편안하셨나요?",
                    "followups": [],
                    "expected_points": ["긴장 완화", "분위기 조성"],
                    "rubric": [
                        {"label": "매우우수", "score": 50, "desc": "편안하고 자연스럽게 대답하며 긍정적인 분위기를 조성함."},
                        {"label": "보통", "score": 30, "desc": "간단하게 대답하며 무난한 수준의 상호작용을 보임."},
                        {"label": "미흡", "score": 10, "desc": "단답형으로 대답하거나 긴장한 기색이 역력함."}
                    ]
                },
                {
                    "id": "intro-2",
                    "question_type": "self_intro",
                    "question": "먼저, 준비하신 자기소개를 부탁드립니다.",
                    "followups": ["가장 강조하고 싶은 경험은 무엇인가요?", "그 경험이 이 직무와 어떻게 연결된다고 생각하시나요?"],
                    "expected_points": ["경력 요약", "직무 관련 강점", "지원 동기"],
                    "rubric": [
                        {"label": "매우우수", "score": 50, "desc": "자신의 핵심 강점과 경험을 직무와 명확히 연결하여 간결하고 설득력 있게 전달함."},
                        {"label": "보통", "score": 30, "desc": "주요 경험을 나열하지만, 직무와의 관련성이나 구체적인 강점 어필이 다소 부족함."},
                        {"label": "미흡", "score": 10, "desc": "자기소개가 너무 길거나 짧고, 직무와 관련 없는 내용이 많음."}
                    ]
                }
            ]
        }
        core_phase = {"phase": "core", "items": core_items}
        wrapup_phase = {
            "phase": "wrapup",
            "items": [
                {
                    "question_type": "wrapup",
                    "question": "마지막으로 저희에게 궁금한 점이 있거나, 하고 싶은 말씀이 있다면 자유롭게 해주세요.",
                    "followups": [],
                    "expected_points": ["회사/직무에 대한 관심도", "입사 의지", "마지막 어필"],
                    "rubric": [
                        {"label": "매우우수", "score": 50, "desc": "회사와 직무에 대한 깊은 관심이 드러나는, 통찰력 있는 질문을 함."},
                        {"label": "보통", "score": 30, "desc": "일반적인 질문(연봉, 복지 등)을 하거나 특별한 질문이 없음."},
                        {"label": "미흡", "score": 10, "desc": "질문이 전혀 없으며, 입사 의지가 부족해 보임."}
                    ]
                }
            ]
        }
        
        return {"phases": [intro_phase, core_phase, wrapup_phase]}

    def design_interview_plan(self) -> Dict:
        """
        Chain-of-Prompts (CoP) 방식으로 LLM을 여러 번 호출하여 인터뷰 계획을 설계합니다.
        """
        if not self.bot.rag_ready:
            return {"error": "RAG system is not ready.", "interview_plan": []}

        print(f"\n🧠 Designing custom interview plan for {self.bot.company_name} via CoP...")
        try:
            contexts = self._get_full_contexts()
            
            # 1단계: 핵심 역량 추출
            competencies = self._extract_competencies(contexts)
            if not competencies:
                raise ValueError("Failed to extract competencies from resume and JD.")
            print(f"✅ Step 1/3: Extracted {len(competencies)} competencies to verify.")

            # 2 & 3단계: 각 역량에 대한 질문 및 루브릭 생성
            core_questions = []
            for i, competency in enumerate(competencies):
                print(f"  - Generating question {i+1}/{len(competencies)} for: {competency}")
                question_item = self._generate_question_for_competency(competency, contexts)
                if question_item:
                    rubric_item = self._create_rubric_for_question(question_item)
                    if rubric_item and "rubric" in rubric_item:
                        question_item.update(rubric_item)
                    core_questions.append(question_item)
            
            if not core_questions:
                raise ValueError("Failed to generate any core questions.")
            print(f"✅ Step 2/3: Generated {len(core_questions)} core questions.")

            # 4단계: 최종 계획 조립
            final_plan_v2 = self._assemble_full_plan(core_questions)
            print("✅ Step 3/3: Assembled final interview plan.")

            # 최종 출력을 새로운 V2 형식과 하위 호환성을 위한 정규화된 형식으로 정리
            
            # 1. 정규화된 형식(normalized_plan) 생성
            transformed_stages = []
            icebreakers = []
            for phase in final_plan_v2.get("phases", []):
                if not isinstance(phase, dict): continue
                
                # `phase` -> `stage` 키 변경 및 `items` -> `questions` 키 변경
                new_stage = {
                    "title": phase.get("phase"),
                    "questions": phase.get("items", [])
                }
                
                # 아이스브레이킹 질문은 별도 리스트로 분리 (호환성을 위해)
                if phase.get("phase") == "intro":
                    non_icebreakers = []
                    for item in new_stage["questions"]:
                        if item.get("question_type") == "icebreaking":
                            icebreakers.append({
                                "id": item.get("id"), "text": item.get("question"),
                                "followups": [], "question_type": "icebreaking"
                            })
                        else:
                            non_icebreakers.append(item)
                    new_stage["questions"] = non_icebreakers
                
                if new_stage["questions"]:
                    transformed_stages.append(new_stage)

            # 2. 최종 반환 객체 생성
            final_plan = {
                "raw_v2_plan": final_plan_v2,
                "normalized_plan": {
                    "icebreakers": icebreakers,
                    "stages": transformed_stages
                }
            }
            
            print("✅ Structured interview plan designed successfully via CoP.")
            return final_plan

        except Exception as e:
            error_msg = f"Failed to design interview plan via CoP: {e}"
            print(f"❌ {error_msg}")
            logger.error(error_msg, exc_info=True)
            return {"error": error_msg, "interview_plan": []}
