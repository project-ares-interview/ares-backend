# ares/api/services/rag/bot/base.py
"""
Base class for the RAG Interview Bot, handling common initialization and low-level API calls.
"""

import json
from typing import Any, Dict, Optional
from functools import lru_cache

from django.conf import settings
from openai import AzureOpenAI

from ares.api.services.prompts import INTERVIEWER_PERSONAS
from ares.api.utils.ai_utils import safe_extract_json
from ..new_azure_rag_llamaindex import AzureBlobRAGSystem
from .utils import _truncate, _escape_special_chars

@lru_cache(maxsize=1)
def get_rag_system_default() -> AzureBlobRAGSystem:
    print("\n📊 Initializing Azure RAG System Connection (Singleton)...")
    return AzureBlobRAGSystem(
        container_name="interview-data",
        index_name="gia-report-index",
    )

class RAGBotBase:
    def __init__(
        self,
        company_name: str,
        job_title: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",
        ncs_context: Optional[dict] = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        rag_system: Optional[AzureBlobRAGSystem] = None,
        **kwargs,
    ):
        print(f"🤖 RAG Bot Base System Initializing (Interviewer: {interviewer_mode})...")
        self.company_name = company_name or "알수없음회사"
        self.job_title = job_title or "알수없음직무"
        self.difficulty = difficulty
        self.interviewer_mode = interviewer_mode
        self.ncs_context = self._ensure_ncs_dict(ncs_context or {})
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

        self.rag_system = rag_system or get_rag_system_default()
        self.rag_ready = self.rag_system.is_ready()
        self._bizinfo_cache: Dict[str, str] = {}

    @staticmethod
    def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
        """NCS 컨텍스트를 항상 dict로 보정"""
        if isinstance(ncs_ctx, dict):
            return ncs_ctx
        if isinstance(ncs_ctx, str):
            try:
                j = json.loads(ncs_ctx)
                if isinstance(j, dict):
                    return j
                return {"ncs": [], "ncs_query": ncs_ctx}
            except Exception:
                return {"ncs": [], "ncs_query": ncs_ctx}
        return {"ncs": [], "ncs_query": ""}

    def _chat_json(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2000) -> Dict[str, Any]:
        sys_msg = {"role": "system", "content": "You must return ONLY a single valid JSON object. No markdown/code fences/commentary."}
        messages = [sys_msg, {"role": "user", "content": prompt}]
        kwargs = dict(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        
        raw_content = ""
        try:
            kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            raw_content = (resp.choices[0].message.content or "").strip()
            return safe_extract_json(raw_content)
        except Exception:
            # 일부 모델 초기 콜에서 json_object 실패시 재시도
            kwargs["messages"] = [sys_msg, {"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON." }]
            kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            raw_content = (resp.choices[0].message.content or "").strip()
            return safe_extract_json(raw_content)

    def _chat(self, prompt: str, temperature: float = 0.4, max_tokens: int = 300) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def _get_company_business_info(self) -> str:
        """회사/직무 맥락 요약 (RAG)"""
        if not self.rag_ready:
            return ""
        try:
            cache_key = f"{self.company_name}::{self.job_title}"
            if cache_key in self._bizinfo_cache:
                return self._bizinfo_cache[cache_key]

            safe_company_name = _escape_special_chars(self.company_name)
            safe_job_title = _escape_special_chars(self.job_title)
            query_text = f"Summarize key business areas, recent performance, major risks for {safe_company_name}, especially related to the {safe_job_title} role."
            print(f"🔍 Querying index '{self.rag_system.index_name}' for company info: {query_text}")
            business_info_raw = self.rag_system.query(query_text)
            summary = _truncate(business_info_raw or "", 1200)
            self._bizinfo_cache[cache_key] = summary
            return summary
        except Exception as e:
            print(f"⚠️ Failed to retrieve company info: {e}")
            return ""
