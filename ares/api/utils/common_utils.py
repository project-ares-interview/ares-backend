"""
공용 유틸 (경로/시간/파일 I/O 등)
- dotenvx로 환경변수 주입함 → 여기서는 os.getenv만 사용
"""

from __future__ import annotations
import os
import json
import datetime
from typing import Any

# 로그 루트 디렉토리 (환경변수로 커스터마이즈 가능)
LOG_ROOT = os.getenv("APP_LOG_ROOT", os.path.join(os.getcwd(), "logs", "interview"))

def ts(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """현재 로컬 시각 문자열 (기본: 2025-09-01 12:34:56)"""
    return datetime.datetime.now().strftime(fmt)

def ensure_dir(path: str) -> None:
    """디렉토리가 없으면 생성 (동시성 안전)"""
    os.makedirs(path, exist_ok=True)

# ---- 선택(편의) 유틸: 파일 I/O ----
def write_text(path: str, content: str, encoding: str = "utf-8") -> None:
    """텍스트 파일 저장 (상위 디렉토리 자동 생성)"""
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding=encoding) as f:
        f.write(content)

def read_text(path: str, encoding: str = "utf-8") -> str:
    """텍스트 파일 읽기 (없으면 빈 문자열)"""
    try:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def save_json(path: str, data: Any, ensure_ascii: bool = False, indent: int = 2) -> None:
    """JSON 저장 (상위 디렉토리 자동 생성)"""
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)

def load_json(path: str) -> Any:
    """JSON 로드 (없으면 None)"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
