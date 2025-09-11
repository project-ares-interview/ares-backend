from __future__ import annotations
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Union, Iterable

from openai import AzureOpenAI

# ========== 환경변수 ==========
AZURE_OAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OAI_KEY         = os.getenv("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_KEY", "")).strip()
AZURE_OAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01").strip()

# Chat용 배포명 (예: gpt-4o-mini 등)
AZURE_OAI_DEPLOYMENT  = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_MODEL", "")).strip()

# Embedding용 배포명 — search_utils.py와 이름을 맞춤
# 1순위: AZURE_OPENAI_EMBEDDING_DEPLOYMENT
# 하위 호환: AZURE_OPENAI_EMBED_DEPLOYMENT_NAME / AZURE_OPENAI_EMBED_MODEL / AZURE_OAI_EMBED_DEPLOYMENT

from __future__ import annotations
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from openai import AzureOpenAI, RateLimitError, APIConnectionError

from ares.api.config import AI_CONFIG

# ========== 환경변수 ========== 
AZURE_OAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OAI_KEY = os.getenv("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_KEY", "")).strip()

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


# 기본 임베딩 모델(미설정 시)
if not AZURE_OAI_EMBED_DEPLOYMENT:
    AZURE_OAI_EMBED_DEPLOYMENT = "text-embedding-3-small"

_client: Optional[AzureOpenAI] = None


# ========== 내부 유틸 ==========
def is_ready() -> bool:
    return bool(AZURE_OAI_ENDPOINT and AZURE_OAI_KEY and AZURE_OAI_DEPLOYMENT)

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
            api_version=AZURE_OAI_API_VERSION,
            azure_endpoint=AZURE_OAI_ENDPOINT,
        )
    return _client

def _retry(func, *, tries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    def wrapper(*args, **kwargs):
        _tries, _delay = tries, delay
        while True:
            try:
                return func(*args, **kwargs)
            except Exception:
                _tries -= 1
                if _tries <= 0:
                    raise
                time.sleep(_delay)
                _delay *= backoff
    return wrapper


# ========== 공개 API ==========
def chat(
    system_or_messages: Union[str, Sequence[Dict[str, Any]]],
    user: Optional[str] = None,
    *,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    stop: Optional[Iterable[str]] = None,
    **kwargs: Any,
) -> str:
    """
    Azure OpenAI Chat 호출 래퍼 (두 가지 호출 방식 모두 지원):
    1) 새 방식: chat("system 메시지", "user 메시지", temperature=..., ...)
    2) 구 방식: chat(messages=[{role, content}, ...], temperature=..., ...)

    반환: 첫 번째 choice의 content 문자열
    """
    client = _get_client()

    # 메시지 구성
    if isinstance(system_or_messages, (list, tuple)):
        messages = list(system_or_messages)
    elif isinstance(system_or_messages, str) and isinstance(user, str):
        messages = [
            {"role": "system", "content": system_or_messages.strip()},
            {"role": "user", "content": user.strip()},
        ]
    else:
        raise TypeError("chat() 호출 형식 오류: (system:str, user:str) 또는 (messages:Sequence[dict])")

    @_retry
    def _call():
        kwargs_payload = dict(
            model=AZURE_OAI_DEPLOYMENT,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs,
        )
        # response_format은 None이면 전달하지 않음(호환성)
        if response_format is not None:
            kwargs_payload["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs_payload)
        return resp.choices[0].message.content or ""

    return _call()


def embed(text_or_texts: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
    """
    Embedding 벡터 생성.
    - 입력: 단일 문자열 또는 문자열 리스트
    - 반환: 단일이면 List[float], 다중이면 List[List[float]]
    """
    client = _get_client()
    model = AZURE_OAI_EMBED_DEPLOYMENT  # 기본: text-embedding-3-small

    inputs: List[str] = [text_or_texts] if isinstance(text_or_texts, str) else list(text_or_texts)

    @_retry
    def _call():
        resp = client.embeddings.create(model=model, input=inputs)
        vectors = [d.embedding for d in resp.data]
        return vectors

    vectors = _call()
    return vectors[0] if isinstance(text_or_texts, str) else vectors


__all__ = ["is_ready", "chat", "embed"]

def embed_batch(texts: List[str]) -> List[List[float]]:
    client = _get_client()
    model = AZURE_OAI_EMBED_DEPLOYMENT
    inputs = [(t or "NCS")[:8000] for t in texts]

    @_retry
    def _call():
        resp = client.embeddings.create(model=model, input=inputs)
        return [d.embedding for d in resp.data]

    return _call()


# ========== 단독 테스트 ==========
if __name__ == "__main__":
    print("[ai_utils] ready:", is_ready())
    if is_ready():
        # 새 방식
        out = chat("한 줄로 답해.", "안녕?")
        print("chat(sys,user):", out)

        # 구 방식
        out2 = chat([
            {"role": "system", "content": "한 줄로 답해."},
            {"role": "user", "content": "반가워?"},
        ])
        print("chat(messages):", out2)

        vec = embed("펌프 정비 절차")
        print("embed dims:", len(vec))
