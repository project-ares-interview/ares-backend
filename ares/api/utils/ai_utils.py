from __future__ import annotations
import os
import time
import json
import re
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from openai import AzureOpenAI, RateLimitError, APIConnectionError

from ares.api.config import AI_CONFIG

# ========== 로거 설정 (수정 완료) ==========
# 1. 우리 앱의 기본 로그 레벨을 INFO로 설정합니다.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 2. 외부 라이브러리의 로그 레벨을 WARNING으로 높여서 상세 정보 로그를 숨깁니다.
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("llama_index.vector_stores.azureaisearch.base").setLevel(logging.WARNING)
# azure 관련 모든 라이브러리를 한 번에 제어하려면 아래와 같이 설정할 수도 있습니다.
# logging.getLogger("azure").setLevel(logging.WARNING)

# 3. 우리 앱의 로거는 그대로 INFO 레벨을 사용하도록 가져옵니다.
logger = logging.getLogger(__name__)


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
                    logger.error(f"API 호출 최종 실패 후 예외 발생: {e}")
                    raise e
                logger.warning(f"API 호출 실패 ({type(e).__name__}). {_delay:.1f}초 후 재시도... ({_tries}회 남음)")
                time.sleep(_delay)
                _delay *= backoff
    return wrapper


# ========== 공개 API ==========
def safe_extract_json(text: str, default: Any = None) -> Any:
    """
    Extract and safely parse a JSON object from a possibly malformed string.

    - Extracts content from markdown code fences (```json ... ```).
    - Falls back to the largest {...} object.
    - Sanitizes common issues (smart quotes, missing commas, trailing commas, True/False/None).
    - Returns `default` (or {}) if parsing fails.
    """
    if not isinstance(text, str) or not text.strip():
        return default if default is not None else {}

    original_text = text
    
    try:
        return json.loads(text)
    except Exception:
        pass

    # 1) ```json ... ``` 블록 우선 추출 (non-greedy)
    fence = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    else:
        # 2) 가장 마지막에 나오는 JSON 객체를 non-greedy하게 추출
        matches = list(re.finditer(r"\{.*?\}", text, re.DOTALL))
        if matches:
            text = matches[-1].group(0)


    # 3) 정규화/치유
    # 스마트 따옴표 등 교정
    text = (
        text.replace("“", '"').replace("”", '"')
            .replace("‘", "'").replace("’", "'")
    )

    # 줄 바꿈 사이에서 누락된 콤마 보정: } 또는 ] 뒤에 바로 "가 오면 콤마 삽입
    text = re.sub(r'([}\]0-9eE"\\])\s*[\r\n]+\s*(")', r"\1,\n\2", text)

    # 공백만 있는 경우도 보정
    text = re.sub(r'([}\]])\s*(")', r'\1,\2', text)

    # 닫는 괄호 앞 트레일링 콤마 제거
    text = re.sub(r',\s*(?=[}\]])', '', text)

    # Python literal -> JSON literal
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    text = text.strip()

    # 4) 파싱
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 파싱 실패. 원본 일부: {original_text[:200]!r} / 정규화본 일부: {text[:200]!r}. 에러: {e}")
        return default if default is not None else {}

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
    processed_inputs = [(t or "NCS")[:max_len] for t in inputs]

    @_retry
    def _call():
        resp = client.embeddings.create(model=model, input=processed_inputs)
        vectors = [d.embedding for d in resp.data]
        return vectors

    vectors = _call()
    return vectors[0] if is_single else vectors


__all__ = ["is_ready", "chat", "embed", "safe_extract_json"]


# ========== 단독 테스트 ==========
if __name__ == "__main__":
    logger.info(f"[ai_utils] ready: {is_ready()}")
    if is_ready():
        out = chat([
            {"role": "system", "content": "한 줄로 답해."},
            {"role": "user", "content": "반가워?"},
        ])
        logger.info(f"chat(messages): {out}")

        vec = embed("펌프 정비 절차")
        logger.info(f"embed dims: {len(vec)}")

        vecs = embed(["펌프 정비", "밸브 수리"])
        logger.info(f"embed_batch dims: {len(vecs[0])}")

        malformed_json_string = """
        ```json
        {
            "key1": "value1",
            "key2": "value2"
            "key3": true,
            "key4": [1, 2, 3,],
        }
        ```
        """
        parsed = safe_extract_json(malformed_json_string)
        logger.info(f"Parsed JSON from malformed string: {parsed}")
        assert parsed.get("key1") == "value1"
        assert parsed.get("key3") is True


from typing import Optional
import re

_END_SENTINEL = "<<END_OF_REPORT>>"
_CODE_FENCE = "```"

def _fences_balanced(s: str) -> bool:
    if not s:
        return True
    return (s.count(_CODE_FENCE) % 2) == 0

def _looks_incomplete(s: str, require_sentinel: bool = False) -> bool:
    if not s:
        return True
    if require_sentinel and _END_SENTINEL not in s:
        return True
    if not _fences_balanced(s):
        return True
    # 한글 연결 조사/접속사로 마무리되면 미완으로 간주 (가벼운 휴리스틱)
    tail = s.strip()[-40:]
    if re.search(r"(으로|로|고|며|지만|그리고|또한|때문에|위해|하며|인데|이지만|인데요|거든요|이라고)$", tail):
        return True
    return False

def chat_complete(
    messages,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1200,
    max_cont: int = 2,
    require_sentinel: bool = False,
    continue_prompt: Optional[str] = None,
    model_kwargs: Optional[dict] = None,
) -> str:
    """
    chat()을 감싸서 '중간 끊김'을 자동 보정하는 래퍼.
    - require_sentinel=True면 결과에 반드시 <<END_OF_REPORT>>가 있어야 완료로 간주.
    - 코드펜스 균형/휴리스틱 기반 문장 미완도 점검.
    - 부족하면 동일 대화 맥락으로 이어서 최대 max_cont번 추가 호출.
    """
    model_kwargs = model_kwargs or {}
    out = chat(messages=messages, temperature=temperature, max_tokens=max_tokens, **model_kwargs) or ""
    acc = out

    cont_msg = continue_prompt or (
        "Continue EXACTLY from the previous character. "
        "Do not repeat any previous text. "
        "Close any open code fences or lists. "
        f"End with a single line containing {_END_SENTINEL}."
    )

    tries = 0
    while tries < max_cont and _looks_incomplete(acc, require_sentinel=require_sentinel):
        tries += 1
        # 대화 맥락에 assistant 직전 응답 + user의 'continue' 프롬프트를 추가하는 식
        follow = messages + [{"role": "assistant", "content": out or ""}, {"role": "user", "content": cont_msg}]
        out = chat(messages=follow, temperature=temperature, max_tokens=max_tokens, **model_kwargs) or ""
        acc += out

    return acc