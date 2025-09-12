from __future__ import annotations
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from openai import AzureOpenAI, RateLimitError, APIConnectionError

from ares.api.config import AI_CONFIG

# ========== 환경변수 ========== 
AZURE_OAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OAI_KEY = os.getenv("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY", "")).strip()

_client: Optional[AzureOpenAI] = None


# ========== 내부 유틸 ========== 
def is_ready() -> bool:
    return bool(AZURE_OAI_ENDPOINT and AZURE_OAI_KEY and AI_CONFIG["CHAT_DEPLOYMENT"])

def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        if not is_ready():
            raise RuntimeError(
                "Azure OpenAI 환경변수 누락: "
                "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / AZURE_OPENAI_DEPLOYMENT_NAME"
            )
        _client = AzureOpenAI(
            api_key=AZURE_OAI_KEY,
            api_version=AI_CONFIG["API_VERSION"],
            azure_endpoint=AZURE_OAI_ENDPOINT,
        )
    return _client

def _retry(func, *, tries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    def wrapper(*args, **kwargs):
        _tries, _delay = tries, delay
        while True:
            try:
                return func(*args, **kwargs)
            except (RateLimitError, APIConnectionError) as e:
                _tries -= 1
                if _tries <= 0:
                    raise e
                time.sleep(_delay)
                _delay *= backoff
    return wrapper


# ========== 공개 API ========== 
def chat(
    messages: Sequence[Dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> str:
    """
    Azure OpenAI Chat 호출 래퍼

    반환: 첫 번째 choice의 content 문자열
    """
    client = _get_client()

    @_retry
    def _call():
        kwargs_payload = dict(
            model=AI_CONFIG["CHAT_DEPLOYMENT"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if response_format is not None:
            kwargs_payload["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs_payload)
        return resp.choices[0].message.content or ""

    return _call()


def embed(text_or_texts: Union[str, List[str]], *, max_len: int = 8000) -> Union[List[float], List[List[float]]]:
    """
    Embedding 벡터 생성.
    - 입력: 단일 문자열 또는 문자열 리스트
    - 반환: 단일이면 List[float], 다중이면 List[List[float]]
    """
    client = _get_client()
    model = AI_CONFIG["EMBED_DEPLOYMENT"]

    is_single = isinstance(text_or_texts, str)
    inputs: List[str] = [text_or_texts] if is_single else list(text_or_texts)
    # 비어있는 텍스트에 대한 기본값 설정 및 길이 제한
    processed_inputs = [(t or "NCS")[:max_len] for t in inputs]

    @_retry
    def _call():
        resp = client.embeddings.create(model=model, input=processed_inputs)
        vectors = [d.embedding for d in resp.data]
        return vectors

    vectors = _call()
    return vectors[0] if is_single else vectors


__all__ = ["is_ready", "chat", "embed"]


# ========== 단독 테스트 ========== 
if __name__ == "__main__":
    print("[ai_utils] ready:", is_ready())
    if is_ready():
        out = chat([
            {"role": "system", "content": "한 줄로 답해."},
            {"role": "user", "content": "반가워?"},
        ])
        print("chat(messages):", out)

        vec = embed("펌프 정비 절차")
        print("embed dims:", len(vec))

        vecs = embed(["펌프 정비", "밸브 수리"])
        print("embed_batch dims:", len(vecs[0]))