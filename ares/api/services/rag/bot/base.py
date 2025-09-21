# ares/api/services/rag/bot/base.py
"""
Base class for the RAG Interview Bot, handling common initialization and low-level API calls.
"""

import re
import json
from typing import Any, Dict, Optional
from unidecode import unidecode

from django.conf import settings
from openai import AzureOpenAI

from ares.api.services.prompts import INTERVIEWER_PERSONAS, prompt_rag_json_correction
from ares.api.utils.ai_utils import safe_extract_json
from ..new_azure_rag_llamaindex import AzureBlobRAGSystem
from .utils import _truncate, _escape_special_chars

def sanitize_for_index(name: str) -> str:
    """Converts a string into a valid Azure AI Search index name."""
    if not name:
        return "default-index"
    # Transliterate to ASCII (e.g., 'SK케미칼' -> 'SKkemikal')
    ascii_name = unidecode(name)
    # Lowercase and replace common separators with a hyphen
    sanitized = ascii_name.lower().replace(' ', '-').replace('_', '-')
    # Remove all other invalid characters (Azure index names are alphanumeric + dashes)
    sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
    # Ensure it doesn't start or end with a hyphen
    sanitized = sanitized.strip('-')
    # Azure index names must be between 2 and 128 chars
    return sanitized[:120] or "default-index"

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

        if rag_system:
            self.rag_system = rag_system
        else:
            sanitized_name = sanitize_for_index(self.company_name)
            dynamic_index_name = f"{sanitized_name}-report-index"
            print(f"\n📊 Initializing Azure RAG System for index: {dynamic_index_name}...")
            self.rag_system = AzureBlobRAGSystem(
                container_name="interview-data",
                index_name=dynamic_index_name,
            )

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

    def _chat_raw_json_str(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2000) -> str:
        """Makes a chat call expecting a JSON response and returns the raw string content."""
        sys_msg = {"role": "system", "content": "You must return ONLY a single valid JSON object. No markdown/code fences/commentary."}
        messages = [sys_msg, {"role": "user", "content": prompt}]
        kwargs = dict(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        
        try:
            kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            # 일부 모델 초기 콜에서 json_object 실패시 재시도
            kwargs["messages"] = [sys_msg, {"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON." }]
            kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()

    def _chat_json(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2000) -> Dict[str, Any]:
        raw_content = self._chat_raw_json_str(prompt, temperature, max_tokens)
        return safe_extract_json(raw_content)

    def _chat_json_correction(self, prompt: str, raw_json: str, max_tokens: int = 4000) -> str:
        """손상된 JSON 응답을 복구하기 위한 LLM 호출"""
        return self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": raw_json or ""}, # raw_json이 None일 경우 빈 문자열로 처리
                {"role": "user", "content": prompt_rag_json_correction},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        ).choices[0].message.content or ""

    def _chat(self, prompt: str, temperature: float = 0.4, max_tokens: int = 300) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def summarize_company_context(self, query_text: str) -> str:
        """회사/직무 맥락 요약 (RAG)"""
        if not self.rag_ready:
            return "RAG 시스템이 준비되지 않았습니다."

        # DART 연동을 위해 sync_index 호출 추가
        if self.company_name:
            print(f"🔄 RAG 인덱스 동기화 시도: {self.company_name}")
            try:
                self.rag_system.sync_index(company_name_filter=self.company_name)
            except Exception as e:
                print(f"⚠️ RAG 인덱스 동기화 중 오류 발생: {e}")
                # 동기화 실패 시에도 계속 진행 (기존 데이터로 질의)

        try:
            print(f"🔍 Querying index '{self.rag_system.index_name}' for company info: {query_text}")
            business_info_raw = self.rag_system.query(query_text)
            summary = _truncate(business_info_raw or "", 1200)
            # 캐싱 로직은 planner에서 처리하므로 여기서는 제거
            return summary
        except Exception as e:
            print(f"⚠️ Failed to retrieve company info: {e}")
            return ""
