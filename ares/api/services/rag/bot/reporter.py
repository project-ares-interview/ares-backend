# ares/api/services/rag/bot/reporter.py
"""
Final Report generator for the RAG Interview Bot.

- 대화 이력, 구조화 점수, 서술 분석을 종합하여 최종 리포트를 생성합니다.
- 출력은 JSON 형식(요약/강점-약점/향후 개선 가이드/스코어 테이블 등)으로 가정합니다.
"""

import json
from typing import Any, Dict, List, Optional

from ares.api.utils.ai_utils import safe_extract_json
from ares.api.services.prompts import (
    prompt_detailed_section,
    prompt_detailed_overview,
    prompt_rag_json_correction,
)
from .base import RAGBotBase
from .utils import _truncate, _debug_print_raw_json

class ReportGenerator:
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def build_report(
        self, 
        transcript: List[Dict[str, Any]], 
        structured_scores: List[Dict[str, Any]],
        interview_plan: Optional[Dict[str, Any]] = None,
        resume_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        2단계 리포트 생성 파이프라인:
        1. 각 문항에 대한 상세 분석 자료(dossier) 생성
        2. 모든 분석 자료를 종합하여 최종 리포트 생성
        """
        try:
            # ==================================================================
            # 1단계: 문항별 상세 분석 자료(dossier) 생성
            # ==================================================================
            per_question_dossiers = []
            
            # transcript에서 질문-답변 쌍을 추출
            qa_pairs = []
            current_pair = {}
            for turn in transcript:
                if turn.get("role") == "interviewer":
                    if current_pair.get("question"): # 이전 쌍이 완성되지 않은 경우를 대비
                        qa_pairs.append(current_pair)
                    current_pair = {"question": turn.get("text"), "id": turn.get("id")}
                elif turn.get("role") == "candidate" and current_pair.get("question"):
                    current_pair["answer"] = turn.get("text")
                    qa_pairs.append(current_pair)
                    current_pair = {}

            # 각 쌍에 대해 상세 분석 AI 호출
            for pair in qa_pairs:
                try:
                    prompt_section = (
                        prompt_detailed_section
                        .replace("{company_name}", self.bot.company_name)
                        .replace("{job_title}", self.bot.job_title)
                        .replace("{persona_description}", self.bot.persona["persona_description"])
                        .replace("{evaluation_focus}", self.bot.persona.get("evaluation_focus", ""))
                        .replace("{business_info}", self.bot._get_company_business_info())
                        .replace("{ncs_titles}", json.dumps(self.bot.ncs_context or {}))
                        .replace("{items}", json.dumps([pair], ensure_ascii=False))
                    )
                    
                    raw_dossier_str = self.bot._chat_raw_json_str(prompt_section, temperature=0.2, max_tokens=3000)
                    dossier_data = safe_extract_json(raw_dossier_str)
                    
                    if dossier_data and "per_question_dossiers" in dossier_data and dossier_data["per_question_dossiers"]:
                        per_question_dossiers.append(dossier_data["per_question_dossiers"][0])
                    else: # 파싱 실패시 복구 시도
                        corrected_str = self.bot._chat_json_correction(prompt_section, raw_dossier_str)
                        dossier_data = safe_extract_json(corrected_str)
                        if dossier_data and "per_question_dossiers" in dossier_data and dossier_data["per_question_dossiers"]:
                            per_question_dossiers.append(dossier_data["per_question_dossiers"][0])

                except Exception as e:
                    print(f"문항별 분석 생성 중 오류 발생 (ID: {pair.get('id')}): {e}")
                    continue # 실패한 문항은 건너뛰고 계속 진행

            # ==================================================================
            # 2단계: 최종 리포트 종합
            # ==================================================================
            transcript_digest = json.dumps(transcript, ensure_ascii=False)
            plan_json = json.dumps(interview_plan or {}, ensure_ascii=False)
            resume_json = json.dumps(resume_feedback or {}, ensure_ascii=False)
            dossiers_json = json.dumps(per_question_dossiers, ensure_ascii=False)

            persona_desc = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
            final_report_goal = "CANDIDATE's overall performance, highlighting strengths, weaknesses, and providing a clear hiring recommendation."

            prompt_overview = (
                prompt_detailed_overview
                .replace("{persona_description}", persona_desc)
                .replace("{final_report_goal}", final_report_goal)
                .replace("{evaluation_focus}", self.bot.persona.get("evaluation_focus", ""))
                .replace("{company_name}", self.bot.company_name)
                .replace("{job_title}", self.bot.job_title)
                .replace("{interview_plan_json}", _truncate(plan_json, 4000))
                .replace("{resume_feedback_json}", _truncate(resume_json, 3000))
                .replace("{transcript_digest}", _truncate(transcript_digest, 8000))
                .replace("{per_question_dossiers}", _truncate(dossiers_json, 12000)) # Dossier가 핵심이므로 길게 허용
            )

            raw_final_str = self.bot._chat_raw_json_str(prompt_overview, temperature=0.3, max_tokens=4000)
            final_result = safe_extract_json(raw_final_str)
            
            if final_result:
                return final_result
            else: # JSON 파싱 실패 시 복구 시도
                _debug_print_raw_json("FINAL_REPORT_FIRST_PASS", raw_final_str or "")
                corrected_raw = self.bot._chat_json_correction(prompt_overview, raw_final_str)
                final_result = safe_extract_json(corrected_raw)
                if final_result:
                    return final_result
                _debug_print_raw_json("FINAL_REPORT_CORRECTION_FAILED", corrected_raw)
                return {"error": "Failed to parse final report after correction"}

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"report_build_failed: {e}"}
