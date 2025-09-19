"""
Base class for the RAG Interview Bot, handling common initialization and low-level API calls.
"""

import json
from typing import Any, Dict, Optional

from django.conf import settings
from openai import AzureOpenAI

from ares.api.services.prompts import INTERVIEWER_PERSONAS
from ..new_azure_rag_llamaindex import AzureBlobRAGSystem
from .utils import _truncate, _escape_special_chars

class RAGBotBase:
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
        sync_on_init: bool = False,
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

        print("\n📊 Initializing Azure RAG System Connection...")
        self.rag_system = None
        self.rag_ready = False
        self._bizinfo_cache: Dict[str, str] = {}

        try:
            self.rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)
            blobs = list(self.rag_system.container_client.list_blobs())
            if not blobs:
                print(f"⚠️ WARNING: Azure Blob container '{container_name}' is empty.")
                return

            print(f"✅ Azure RAG system ready, based on {len(blobs)} documents.")
            if sync_on_init:
                print("🔄 Syncing Azure AI Search index (sync_on_init=True)...")
                self.rag_system.sync_index(company_name_filter=self.company_name)
            else:
                print("⏩ Skipping index sync (sync_on_init=False). Should be managed externally.")

            self.rag_ready = True

        except Exception as e:
            print(f"❌ Failed to connect to RAG system: {e}")

    @staticmethod
    def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
        """
        Ensures the NCS context is always a dictionary, regardless of input type.
        """
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

    def _chat_json(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2000) -> str:
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
                messages=[sys_msg, {"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON." }],
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

    def _get_company_business_info(self) -> str:
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

