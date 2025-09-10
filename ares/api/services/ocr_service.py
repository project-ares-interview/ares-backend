"""
Azure Document Intelligence (prebuilt-read) OCR 서비스
- 필요 ENV: AZURE_DI_ENDPOINT, AZURE_DI_KEY
- 동기 방식. PDF/PNG/JPG/JPEG 지원(바이너리 업로드 및 URL)
"""
from __future__ import annotations
import os
import time
import mimetypes
import requests
from typing import Optional, Tuple
from urllib.parse import urlparse
from ares.api.utils.common_utils import get_logger, retry

# --------- 환경/로깅 ----------
AZURE_DI_ENDPOINT: str = os.getenv("AZURE_DI_ENDPOINT", "").strip()  # e.g. https://xxx.cognitiveservices.azure.com
AZURE_DI_KEY: str = os.getenv("AZURE_DI_KEY", "").strip()
AZURE_DI_API_VERSION: str = os.getenv("AZURE_DI_API_VERSION", "2024-07-31-preview").strip()

OCR_MAX_FILE_MB: int = int(os.getenv("OCR_MAX_FILE_MB", "20"))  # 안전 상한
POLL_DEFAULT_TIMEOUT_S: float = float(os.getenv("OCR_POLL_TIMEOUT_S", "120"))
POLL_MIN_INTERVAL_S: float = 0.5
POLL_MAX_INTERVAL_S: float = 5.0

_log = get_logger("ocr")

# --------- 내부 유틸 ----------
def _validate_env():
    if not (AZURE_DI_ENDPOINT and AZURE_DI_KEY):
        raise RuntimeError("Azure DI 환경변수 필요(AZURE_DI_ENDPOINT, AZURE_DI_KEY).")

def _normalize_endpoint(ep: str) -> str:
    ep = (ep or "").strip().rstrip("/")
    if not ep.startswith("http"):
        raise ValueError(f"잘못된 엔드포인트: {ep}")
    return ep

def _headers_json():
    return {"Ocp-Apim-Subscription-Key": AZURE_DI_KEY, "Content-Type": "application/json"}

def _headers_bin(content_type: str = "application/octet-stream"):
    return {"Ocp-Apim-Subscription-Key": AZURE_DI_KEY, "Content-Type": content_type or "application/octet-stream"}

def _analyze_url() -> str:
    base = _normalize_endpoint(AZURE_DI_ENDPOINT)
    return f"{base}/documentintelligence/documentModels/prebuilt-read:analyze?api-version={AZURE_DI_API_VERSION}"

def _sleep_interval(resp: requests.Response) -> float:
    # Retry-After 우선, 범위 클램프
    try:
        ra = resp.headers.get("Retry-After")
        if ra:
            s = float(ra)
            return max(POLL_MIN_INTERVAL_S, min(POLL_MAX_INTERVAL_S, s))
    except Exception:
        pass
    return POLL_MIN_INTERVAL_S

def _is_valid_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False

def _guess_mime_from_path(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"

def _extract_text(data: dict) -> Tuple[str, Optional[str], int]:
    """
    반환: (text, language, page_count)
    - paragraphs 우선 → 없으면 lines
    - 페이지 구분을 빈 줄로 유지
    """
    ar = data.get("analyzeResult", {}) or {}
    lang = None
    try:
        # language가 batches/paragraphs/pages 어디에 있을 수 있음
        lang = (ar.get("documents") or [{}])[0].get("language") or ar.get("languages", [{}])[0].get("locale")
    except Exception:
        pass

    pages = ar.get("pages", []) or []
    # paragraphs가 있으면 그걸 우선 사용
    paras = ar.get("paragraphs", []) or []
    if paras:
        # paragraph는 page 번호를 포함하므로, 페이지 순서대로 묶는다
        by_page = {}
        for p in paras:
            pg = int(p.get("spans", [{}])[0].get("offset", 0))  # offset 기반이지만 실제 pageNumber 접근이 없을 수 있어 보정
            txt = (p.get("content") or "").strip()
            if not txt:
                continue
            by_page.setdefault(pg, []).append(txt)
        # 페이지 순서를 안정적으로 만들기 위해 키 정렬
        lines = []
        for _, items in sorted(by_page.items(), key=lambda kv: kv[0]):
            lines.extend(items)
            lines.append("")  # 페이지 구분
        text = "\n".join(lines).strip()
        return text, lang, len(pages) or max(1, len(by_page))
    else:
        # lines 기반
        lines = []
        for p in pages:
            for l in p.get("lines", []) or []:
                t = (l.get("content") or "").strip()
                if t:
                    lines.append(t)
            lines.append("")  # 페이지 구분
        text = "\n".join(lines).strip()
        return text, lang, len(pages) or 0

def _enforce_size_limit(byte_len: int):
    max_bytes = OCR_MAX_FILE_MB * 1024 * 1024
    if byte_len > max_bytes:
        raise RuntimeError(f"OCR 파일 크기 초과: {byte_len} bytes > {max_bytes} bytes (limit {OCR_MAX_FILE_MB}MB)")

def _post_analyze_binary(data: bytes, content_type: str) -> str:
    _validate_env()
    _enforce_size_limit(len(data))
    r = requests.post(_analyze_url(), headers=_headers_bin(content_type), data=data, timeout=60)
    r.raise_for_status()
    op_url = r.headers.get("operation-location") or r.headers.get("Operation-Location")
    if not op_url:
        raise RuntimeError("operation-location 헤더 없음")
    result = _poll(op_url, timeout=POLL_DEFAULT_TIMEOUT_S)
    if not result or result.get("status") != "succeeded":
        reason = (result or {}).get("error", {})
        raise RuntimeError(f"OCR 실패: {reason}")
    text, lang, page_count = _extract_text(result)
    _log.info(f"OCR 완료: bytes={len(data)}, pages={page_count}, lang={lang}, length={len(text)}")
    return text

def _post_analyze_url(url: str) -> str:
    _validate_env()
    if not _is_valid_url(url):
        raise ValueError(f"유효하지 않은 URL: {url}")
    payload = {"urlSource": url}
    r = requests.post(_analyze_url(), headers=_headers_json(), json=payload, timeout=60)
    r.raise_for_status()
    op_url = r.headers.get("operation-location") or r.headers.get("Operation-Location")
    if not op_url:
        raise RuntimeError("operation-location 헤더 없음")
    result = _poll(op_url, timeout=POLL_DEFAULT_TIMEOUT_S)
    if not result or result.get("status") != "succeeded":
        reason = (result or {}).get("error", {})
        raise RuntimeError(f"OCR 실패: {reason}")
    text, lang, page_count = _extract_text(result)
    _log.info(f"OCR URL 완료: {url}, pages={page_count}, lang={lang}, length={len(text)}")
    return text

def _poll(op_url: str, timeout: float = POLL_DEFAULT_TIMEOUT_S) -> Optional[dict]:
    start = time.time()
    while True:
        r = requests.get(op_url, headers={"Ocp-Apim-Subscription-Key": AZURE_DI_KEY}, timeout=30)
        r.raise_for_status()
        data = r.json()
        st = data.get("status")
        if st in ("succeeded", "failed"):
            return data
        if time.time() - start > timeout:
            _log.error(f"OCR Polling timeout({timeout}s) op={op_url}")
            return None
        time.sleep(_sleep_interval(r))

# --------- 공개 API (드롭인 호환) ----------
@retry()
def di_analyze_file(path: str, poll_timeout: float = POLL_DEFAULT_TIMEOUT_S) -> str:
    """
    로컬 파일 경로 기반 OCR
    """
    _validate_env()
    with open(path, "rb") as f:
        data = f.read()
    content_type = _guess_mime_from_path(path)
    # 호출부의 poll_timeout을 반영하고자 로컬 상수 변경
    global POLL_DEFAULT_TIMEOUT_S
    prev = POLL_DEFAULT_TIMEOUT_S
    POLL_DEFAULT_TIMEOUT_S = poll_timeout
    try:
        return _post_analyze_binary(data, content_type)
    finally:
        POLL_DEFAULT_TIMEOUT_S = prev

@retry()
def di_analyze_url(url: str, poll_timeout: float = POLL_DEFAULT_TIMEOUT_S) -> str:
    """
    원격 URL 기반 OCR (SAS/공개 URL)
    """
    _validate_env()
    global POLL_DEFAULT_TIMEOUT_S
    prev = POLL_DEFAULT_TIMEOUT_S
    POLL_DEFAULT_TIMEOUT_S = poll_timeout
    try:
        return _post_analyze_url(url)
    finally:
        POLL_DEFAULT_TIMEOUT_S = prev

# 추가: 메모리 바이트 스트림 OCR (상층에서 파일 저장 없이 처리 가능)
@retry()
def di_analyze_bytes(data: bytes, content_type: str = "application/octet-stream", poll_timeout: float = POLL_DEFAULT_TIMEOUT_S) -> str:
    _validate_env()
    global POLL_DEFAULT_TIMEOUT_S
    prev = POLL_DEFAULT_TIMEOUT_S
    POLL_DEFAULT_TIMEOUT_S = poll_timeout
    try:
        return _post_analyze_binary(data, content_type)
    finally:
        POLL_DEFAULT_TIMEOUT_S = prev

# --------- CLI 테스트 ----------
if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser(description="Azure DI OCR 테스트")
    p.add_argument("--path", help="로컬 파일 경로")
    p.add_argument("--url", help="이미지/PDF URL")
    p.add_argument("--timeout", type=float, default=POLL_DEFAULT_TIMEOUT_S)
    args = p.parse_args()
    try:
        if args.path:
            print(di_analyze_file(args.path, poll_timeout=args.timeout)[:2000])
        elif args.url:
            print(di_analyze_url(args.url, poll_timeout=args.timeout)[:2000])
        else:
            print("사용법: --path 또는 --url")
    except Exception as e:
        _log.error(f"OCR 실행 실패: {e}")
        sys.exit(1)
