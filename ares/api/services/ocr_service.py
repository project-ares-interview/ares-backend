# ares/api/services/ocr_service.py
"""
Azure Document Intelligence (prebuilt-read) OCR 서비스
- dotenvx로 환경변수 주입됨: AZURE_DI_ENDPOINT, AZURE_DI_KEY
- 동기 방식. PDF/PNG/JPG/JPEG 지원(바이너리 업로드).
"""

from __future__ import annotations
import os
import time
import requests
from typing import Optional

AZURE_DI_ENDPOINT: str = os.getenv("AZURE_DI_ENDPOINT", "").strip()
AZURE_DI_KEY: str = os.getenv("AZURE_DI_KEY", "").strip()

# 팀 표준 API 버전
_API_VERSION = "2023-07-31"

def _analyze_url() -> str:
    if not AZURE_DI_ENDPOINT:
        return ""
    base = AZURE_DI_ENDPOINT.rstrip("/")
    return f"{base}/formrecognizer/documentModels/prebuilt-read:analyze?api-version={_API_VERSION}"

def di_analyze_file(file_path: str, *, timeout: int = 60, poll_timeout: int = 60) -> str:
    """
    파일 경로 입력 → OCR 텍스트(str)
    - timeout: 최초 analyze 요청 타임아웃(초)
    - poll_timeout: 상태 조회 총 대기(초)
    반환: 추출 텍스트(없으면 빈 문자열)
    """
    if not (AZURE_DI_ENDPOINT and AZURE_DI_KEY):
        return ""

    url = _analyze_url()
    if not url:
        return ""

    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": AZURE_DI_KEY,
                    "Content-Type": "application/octet-stream",
                },
                data=f.read(),
                timeout=timeout,
            )
        resp.raise_for_status()
        op = resp.headers.get("operation-location")
        if not op:
            return ""

        # poll
        started = time.time()
        while True:
            r = requests.get(
                op,
                headers={"Ocp-Apim-Subscription-Key": AZURE_DI_KEY},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            st = data.get("status")
            if st in ("succeeded", "failed"):
                break
            if time.time() - started > poll_timeout:
                return ""
            time.sleep(0.75)

        if st != "succeeded":
            return ""

        # 결과 파싱
        analyze = data.get("analyzeResult", {})
        pages = analyze.get("pages", [])
        lines: list[str] = []
        for p in pages:
            for line in p.get("lines", []):
                txt = line.get("content", "")
                if txt:
                    lines.append(txt)
        return "\n".join(lines).strip()

    except Exception as e:
        print("[DI OCR 오류]", e)
        return ""

if __name__ == "__main__":
    # 간단 자가 테스트 (선택)
    import sys
    if len(sys.argv) < 2:
        print("사용법: python -m ares.api.services.ocr_service <파일경로>")
        sys.exit(0)
    print(di_analyze_file(sys.argv[1])[:500])
