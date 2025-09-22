# ares/api/services/rag/bot/reporter.py
"""
Final Report generator for the RAG Interview Bot.

- 대화 이력, 구조화 점수, 서술 분석을 종합하여 최종 리포트를 생성합니다.
- 출력은 JSON 형식(요약/강점-약점/향후 개선 가이드/스코어 테이블 등)으로 가정합니다.
"""

import json
from typing import Any, Dict, List, Optional
import numpy as np

from ares.api.utils.ai_utils import safe_extract_json
from ares.api.services.prompts import (
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
        resume_feedback: Optional[Dict[str, Any]] = None,
        full_contexts: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        최종 리포트 생성 파이프라인:
        - 상세 분석된 structured_scores를 '미시적' 데이터로 가공하고,
        - 전체 transcript와 full_contexts를 '거시적' 데이터로 활용하여 최종 리포트를 종합합니다.
        """
        try:
            # ==================================================================
            # 1단계: '미시적' 데이터 가공 (structured_scores -> per_question_dossiers)
            # ==================================================================
            per_question_dossiers = []
            for score_data in structured_scores:
                # 상세 분석 결과에서 필요한 정보 추출
                question_id = score_data.get("question_id", "N/A")
                question_text = score_data.get("question", "N/A")
                question_intent = score_data.get("question_intent", "N/A")
                
                # evaluation 객체 재구성
                evaluation = {
                    "applied_framework": score_data.get("scoring", {}).get("framework", "N/A"),
                    "scores_main": score_data.get("scoring", {}).get("scores_main", {}),
                    "scores_ext": score_data.get("scoring", {}).get("scores_ext", {}),
                    "feedback": score_data.get("feedback", "N/A"),
                    "evidence_quote": score_data.get("answer", "") # 답변 전체 인용으로 변경
                }
                
                # model_answer 객체 재구성
                model_answer = score_data.get("model_answer", {}).get("model_answer", "N/A")

                # 최종 dossier 객체 생성
                dossier = {
                    "question_id": question_id,
                    "question": question_text,
                    "question_intent": question_intent,
                    "evaluation": evaluation,
                    "model_answer": model_answer,
                    "coaching": score_data.get("coaching", {}),
                }
                per_question_dossiers.append(dossier)

            # ==================================================================
            # 2단계: 최종 리포트 종합 ('거시적' 관점 추가)
            # ==================================================================
            transcript_digest = json.dumps(transcript, ensure_ascii=False)
            plan_json = json.dumps(interview_plan or {}, ensure_ascii=False)
            resume_json = json.dumps(resume_feedback or {}, ensure_ascii=False)
            dossiers_json = json.dumps(per_question_dossiers, ensure_ascii=False)
            contexts_json = json.dumps(full_contexts or {}, ensure_ascii=False)

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
                .replace("{per_question_dossiers}", _truncate(dossiers_json, 32000)) # Increase limit to avoid breaking JSON
                .replace("{full_contexts_json}", _truncate(contexts_json, 8000))
            )

            raw_final_str = self.bot._chat_raw_json_str(prompt_overview, temperature=0.3, max_tokens=4000)
            final_result = safe_extract_json(raw_final_str)

            if final_result:
                # [수정] AI가 생성한 피드백 리스트를 코드 기반의 전체 리스트로 완전히 교체합니다.
                # 이 방식은 AI가 토큰 제한이나 다른 이유로 리스트를 누락/요약하는 것을 원천적으로 방지하여,
                # 항상 모든 질문에 대한 피드백이 완전하게 포함되도록 보장합니다.
                final_result['question_by_question_feedback'] = per_question_dossiers
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
