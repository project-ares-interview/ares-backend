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
from typing import Any, Callable
from datetime import datetime
from functools import wraps

# 로그 루트 디렉토리 (환경변수로 커스터마이즈 가능)
LOG_ROOT = os.getenv("APP_LOG_ROOT", os.path.join(os.getcwd(), "logs", "ares"))
os.makedirs(LOG_ROOT, exist_ok=True)

def ts(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now().strftime(fmt)

def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def read_text(path: str, encoding: str = "utf-8") -> str:
    try:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

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

# ---- 로깅 ----
_loggers_cache: dict[str, logging.Logger] = {}

def get_logger(name: str = "ares", level: str | None = None) -> logging.Logger:
    if name in _loggers_cache:
        return _loggers_cache[name]
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    fh = logging.FileHandler(os.path.join(LOG_ROOT, f"{name}.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    # 중복 핸들러 방지
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)

    _loggers_cache[name] = logger
    return logger

# ---- 재시도(지수 백오프) ----
def retry(exceptions: tuple[type[BaseException], ...] = (Exception,),
          tries: int = 3, delay: float = 0.6, backoff: float = 2.0, logger: logging.Logger | None = None):
    def deco(fn: Callable):
        @wraps(fn)
        def wrapped(*a, **kw):
            _delay = delay
            last = None
            for i in range(tries):
                try:
                    return fn(*a, **kw)
                except exceptions as e:
                    last = e
                    (logger or get_logger("ares")).warning(f"{fn.__name__} 실패({i+1}/{tries}): {e}")
                    if i == tries - 1:
                        break
                    time.sleep(_delay)
                    _delay *= backoff
            raise last  # 최종 실패는 상위에서 처리
        return wrapped
    return deco

# ---- 텍스트 유틸 ----
def chunk_text(s: str, chunk: int = 6000, overlap: int = 400):
    s = s or ""
    if len(s) <= chunk:
        yield s
        return
    step = max(1, chunk - overlap)
    for i in range(0, len(s), step):
        yield s[i:i+chunk]
