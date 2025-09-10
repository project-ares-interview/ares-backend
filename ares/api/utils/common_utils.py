"""
공용 유틸 (경로/시간/파일 I/O/로깅/재시도 등)
- dotenvx로 환경변수 주입됨 → 여기서는 os.getenv만 사용
"""

from __future__ import annotations
import os
import sys
import json
import time
import logging
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Iterable, Optional
from datetime import datetime
from functools import wraps
import random

# ---------------------------
# 경로/시간
# ---------------------------
LOG_ROOT = os.getenv("APP_LOG_ROOT", os.path.join(os.getcwd(), "logs", "ares"))
os.makedirs(LOG_ROOT, exist_ok=True)

def ts(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now().strftime(fmt)

def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

# ---------------------------
# 파일 I/O
# ---------------------------
def read_text(path: str, encoding: str = "utf-8") -> str:
    try:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def save_text(path: str, text: str, encoding: str = "utf-8") -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding=encoding) as f:
        f.write(text or "")

def save_json(path: str, data: Any, ensure_ascii: bool = False, indent: int = 2) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)

def load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def safe_json_loads(s: str, default: Any = None):
    try:
        return json.loads(s)
    except Exception:
        return default

# JSONL 헬퍼 (추가)
def append_jsonl(path: str, rows: Iterable[dict]) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "a", encoding="utf-8") as f:
        for r in rows or []:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def iter_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except Exception:
                    continue

# ---------------------------
# 로깅
# ---------------------------
_loggers_cache: dict[str, logging.Logger] = {}

def get_logger(name: str = "ares", level: str | None = None) -> logging.Logger:
    """
    - 캐시/재호출 시에도 level 파라미터가 주어지면 logger 레벨을 갱신
    - 파일 로그는 로테이션 적용 (기본 10MB, 백업 5개)
    - 중복 핸들러 방지
    """
    lvl_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    lvl = getattr(logging, lvl_name, logging.INFO)

    if name in _loggers_cache:
        logger = _loggers_cache[name]
        if logger.level != lvl:
            logger.setLevel(lvl)
        return logger

    logger = logging.getLogger(name)
    logger.setLevel(lvl)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # 파일 핸들러 (로테이션)
    max_bytes = int(os.getenv("LOG_ROTATE_BYTES", str(10 * 1024 * 1024)))  # 10MB
    backup_cnt = int(os.getenv("LOG_ROTATE_BACKUPS", "5"))
    fh = RotatingFileHandler(
        os.path.join(LOG_ROOT, f"{name}.log"),
        maxBytes=max_bytes,
        backupCount=backup_cnt,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)

    # 콘솔 핸들러
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)

    _loggers_cache[name] = logger
    return logger

# ---------------------------
# 재시도(지수 백오프 + 지터)
# ---------------------------
def retry(exceptions: tuple[type[BaseException], ...] = (Exception,),
          tries: int = 3, delay: float = 0.6, backoff: float = 2.0,
          jitter: float = 0.25,
          logger: logging.Logger | None = None,
          on_retry: Callable[[int, BaseException], None] | None = None):
    """
    exceptions: 재시도 대상 예외 튜플
    jitter: 각 시도 간 대기시간에 [0, jitter] 랜덤 가산
    on_retry: 콜백(on_retry(attempt_index, exception))
    """
    def deco(fn: Callable):
        @wraps(fn)
        def wrapped(*a, **kw):
            _delay = delay
            last: Optional[BaseException] = None
            for i in range(tries):
                try:
                    return fn(*a, **kw)
                except exceptions as e:
                    last = e
                    (logger or get_logger("ares")).warning(f"{fn.__name__} 실패({i+1}/{tries}): {e}")
                    if on_retry:
                        try:
                            on_retry(i + 1, e)
                        except Exception:
                            pass
                    if i == tries - 1:
                        break
                    time.sleep(_delay + random.uniform(0, max(0.0, jitter)))
                    _delay *= backoff
            raise last  # type: ignore[misc]
        return wrapped
    return deco

# ---------------------------
# 텍스트 유틸
# ---------------------------
def chunk_text(s: str, chunk: int = 6000, overlap: int = 400, limit_chunks: Optional[int] = None):
    """
    긴 텍스트를 겹침 포함 슬라이스.
    - limit_chunks: 최대 청크 수 제한(프롬프트 가드)
    """
    s = s or ""
    if len(s) <= chunk:
        yield s
        return
    step = max(1, chunk - overlap)
    count = 0
    for i in range(0, len(s), step):
        if limit_chunks is not None and count >= limit_chunks:
            break
        yield s[i:i+chunk]
        count += 1
