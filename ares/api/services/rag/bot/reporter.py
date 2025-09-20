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
    prompt_rag_final_report,
    prompt_rag_json_correction,
)
from .base import RAGBotBase
from .utils import _truncate, _debug_print_raw_json

class ReportGenerator:
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def build_report(self, transcript: List[Dict[str, Any]], structured_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        transcript: [{"role":"interviewer"/"candidate", "text":"..." , "id":"1-1", ...}, ...]
        structured_scores: [analyzer._structured_evaluation 결과 축약본 등]
        """
        try:
            convo_preview = []
            for turn in transcript[-20:]:  # 최근 20턴만 요약에 반영
                role = turn.get("role") or ""
                text = _truncate(turn.get("text") or "", 500)
                qid = turn.get("id")
                if qid:
                    convo_preview.append(f"[{qid}] {role}: {text}")
                else:
                    convo_preview.append(f"{role}: {text}")

            persona_desc = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)

            prompt = (
                prompt_rag_final_report
                .replace("{persona_description}", persona_desc)
                .replace("{company_name}", self.bot.company_name)
                .replace("{job_title}", self.bot.job_title)
                .replace("{conversation_preview}", "\n".join(convo_preview))
                .replace("{structured_scores}", _truncate(json.dumps(structured_scores, ensure_ascii=False), 3500))
            )

            raw = self.bot._chat_json(prompt, temperature=0.2, max_tokens=2500)
            result = safe_extract_json(raw)
            if result is not None:
                return result

            _debug_print_raw_json("REPORT_FIRST_PASS", raw or "")
            corrected_raw = self.bot.client.chat.completions.create(
                model=self.bot.model,
                messages=[
                    {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": prompt_rag_json_correction},
                ],
                temperature=0.0,
                max_tokens=2500,
                response_format={"type": "json_object"},
            ).choices[0].message.content or ""
            final_result = safe_extract_json(corrected_raw)
            if final_result is not None:
                return final_result
            _debug_print_raw_json("REPORT_CORRECTION_FAILED", corrected_raw)
            return {"error": "Failed to parse report after correction"}

        except Exception as e:
            return {"error": f"report_build_failed: {e}"}
