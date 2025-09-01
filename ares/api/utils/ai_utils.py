# utils/ai_utils.py
from __future__ import annotations
import os
from typing import List, Dict, Any, Optional
from openai import AzureOpenAI

AZURE_OAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OAI_KEY         = os.getenv("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_KEY","")).strip()
AZURE_OAI_DEPLOYMENT  = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_MODEL","")).strip()
AZURE_OAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01").strip()

_client: Optional[AzureOpenAI] = None

def get_client() -> Optional[AzureOpenAI]:
    global _client
    if _client is None and AZURE_OAI_ENDPOINT and AZURE_OAI_KEY and AZURE_OAI_DEPLOYMENT:
        _client = AzureOpenAI(
            azure_endpoint=AZURE_OAI_ENDPOINT,
            api_key=AZURE_OAI_KEY,
            api_version=AZURE_OAI_API_VERSION,
        )
    return _client

def chat(messages: List[Dict[str, Any]], temperature: float = 0.4, max_tokens: int = 256) -> str:
    client = get_client()
    if not client:
        return "Azure OpenAI 설정 오류"
    try:
        r = client.chat.completions.create(
            model=AZURE_OAI_DEPLOYMENT,
            temperature=temperature,
            messages=messages,
            max_tokens=max_tokens,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        return f"[오류] {e}"

if __name__ == "__main__":
    if not get_client():
        print("Azure OpenAI 환경변수 누락")
    else:
        print(chat([{"role":"user","content":"ping"}], max_tokens=5))