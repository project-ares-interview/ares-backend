"""
파일 처리 유틸
- TXT, DOCX, PDF/이미지 → 텍스트 추출 (PDF는 텍스트 추출 우선, 실패 시 OCR 폴백)
- 가상 문서(붙여넣기 텍스트) 관리 (이름 충돌 방지)
- 이력서/자소서 자동 분리(오탐 방지 규칙 + 선택적 메타)
"""
from __future__ import annotations

import os, re, mimetypes
from typing import Dict, List, Tuple, Optional, Iterable, Any
from ares.api.utils.common_utils import get_logger, read_text, ensure_dir  # I/O/로그 유틸
from ares.api.services.ocr_service import di_analyze_file

_log = get_logger("file")

# =========================
# 환경 가드(대용량/제한)
# =========================
MAX_FILE_BYTES = int(os.getenv("FILEUTILS_MAX_FILE_BYTES", str(50 * 1024 * 1024)))  # 50MB
PDF_MAX_TEXT_PAGES = int(os.getenv("FILEUTILS_PDF_MAX_TEXT_PAGES", "10"))  # 텍스트 추출 시도 페이지 상한
VIRTUAL_TEXT_MAX_CHARS = int(os.getenv("FILEUTILS_VIRTUAL_TEXT_MAX_CHARS", "50000"))

# =========================
# 내부: 텍스트 정규화
# =========================
_WS_RE = re.compile(r"[ \t]+\n")      # 줄 끝 공백
_MULTI_NL = re.compile(r"\n{3,}")     # 연속 빈 줄
def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _WS_RE.sub("\n", s)
    s = _MULTI_NL.sub("\n\n", s)
    return s.strip()

# =========================
# 파일 판별
# =========================
_IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
_TXT_EXT = {".txt", ".md", ".markdown", ".csv", ".log"}
_DOCX_EXT = {".docx"}
_PDF_EXT = {".pdf"}

def _guess_mime(path: str) -> str:
    m, _ = mimetypes.guess_type(path)
    return m or ""

def _size_ok(fp: str) -> bool:
    try:
        return os.path.getsize(fp) <= MAX_FILE_BYTES
    except Exception:
        return True  # 크기 확인 실패 시 일단 진행

# =========================
# 파일 읽기 구현
# =========================
def _read_docx(fp: str) -> str:
    try:
        import docx
    except Exception:
        _log.warning("python-docx 미설치: DOCX 읽기 불가")
        return ""
    try:
        doc = docx.Document(fp)
        blocks: List[str] = []
        # 본문
        for p in doc.paragraphs:
            if p.text and p.text.strip():
                blocks.append(p.text.strip())
        # 표(있으면)
        for t in getattr(doc, "tables", []):
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        return _normalize_text("\n".join(blocks))
    except Exception as e:
        _log.error(f"DOCX 읽기 실패: {e}")
        return ""

def _read_pdf_text_first(fp: str, max_pages: Optional[int] = None) -> str:
    """
    PDF에서 텍스트 추출(가능하면). 실패/무의미하면 빈 문자열 반환.
    - pdfplumber → pypdf 순 폴백
    """
    text = ""
    try:
        import pdfplumber  # 더 정확한 추출
        with pdfplumber.open(fp) as pdf:
            pages = pdf.pages[:max_pages] if max_pages else pdf.pages
            text = "\n".join([(p.extract_text() or "").strip() for p in pages])
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(fp)
            n = len(reader.pages)
            limit = min(n, max_pages) if max_pages else n
            buf: List[str] = []
            for i in range(limit):
                try:
                    buf.append((reader.pages[i].extract_text() or "").strip())
                except Exception:
                    continue
            text = "\n".join(buf)
        except Exception:
            text = ""
    # 너무 빈약하면 OCR 단계로 넘김
    if text and len(text.strip()) >= 40:
        return _normalize_text(text)
    return ""

def _read_pdf_or_image(fp: str, *, max_pages_for_text: Optional[int] = PDF_MAX_TEXT_PAGES) -> str:
    try:
        if not _size_ok(fp):
            _log.warning(f"파일 크기 초과로 스킵: {os.path.basename(fp)}")
            return ""

        ext = os.path.splitext(fp)[1].lower()
        if ext in _PDF_EXT:
            # 1) 텍스트 추출 시도
            txt = _read_pdf_text_first(fp, max_pages_for_text)
            if txt:
                return txt
            # 2) OCR 폴백
            _log.info(f"PDF 텍스트 추출 미미 → OCR 폴백: {os.path.basename(fp)}")
            ocr = di_analyze_file(fp)
            return _normalize_text(ocr or "")
        # 이미지류는 바로 OCR
        if ext in _IMG_EXT:
            ocr = di_analyze_file(fp)
            return _normalize_text(ocr or "")
        # MIME 힌트로 보조
        mime = _guess_mime(fp)
        if mime.startswith("image/"):
            ocr = di_analyze_file(fp)
            return _normalize_text(ocr or "")
        if mime == "application/pdf":
            txt = _read_pdf_text_first(fp, max_pages_for_text)
            if txt:
                return txt
            ocr = di_analyze_file(fp)
            return _normalize_text(ocr or "")
        return ""
    except Exception as e:
        _log.error(f"OCR 실패({fp}): {e}")
        return ""

def read_file_auto(fp: str) -> str:
    ext = os.path.splitext(fp)[1].lower()
    if ext in _TXT_EXT:
        return _normalize_text(read_text(fp))
    if ext in _DOCX_EXT:
        return _read_docx(fp)
    if ext in _PDF_EXT or ext in _IMG_EXT:
        return _read_pdf_or_image(fp)
    # 기타 → 텍스트로 가정(예: .json, .yml 등)
    return _normalize_text(read_text(fp))

# =========================
# 컨텍스트 수집
# =========================
def collect_context(files: List[str]) -> Tuple[str, Dict[str, str]]:
    """
    다수 파일에서 텍스트를 읽어 합치고, 파일명:텍스트 dict도 반환
    (LLM 컨텍스트로 넣기 좋게 파일 헤더를 섹션 형태로 합침)
    """
    merged: List[str] = []
    per_file: Dict[str, str] = {}
    for fp in files or []:
        if not fp or not os.path.exists(fp):
            continue
        txt = read_file_auto(fp)
        if txt and txt.strip():
            base = os.path.basename(fp)
            per_file[base] = txt.strip()
            merged.append(f"# [{base}]\n{txt.strip()}")
    return ("\n\n".join(merged)).strip(), per_file

# =========================
# 붙여넣기(가상) 문서 관리
# =========================
def _dedupe_name(per_file: Dict[str, str], name: str) -> str:
    base, ext = os.path.splitext(name)
    n = 1
    out = name
    while out in per_file:
        n += 1
        out = f"{base}_{n}{ext}"
    return out

def virtual_append(per_file: Dict[str, str], virt_name: str, text: str) -> None:
    if text and str(text).strip():
        s = str(text).strip()
        if len(s) > VIRTUAL_TEXT_MAX_CHARS:
            _log.warning(f"가상 문서 길이 초과({len(s)}>{VIRTUAL_TEXT_MAX_CHARS}) → 절단 저장")
            s = s[:VIRTUAL_TEXT_MAX_CHARS]
        name = _dedupe_name(per_file, virt_name or "Pasted.txt")
        per_file[name] = s

def join_texts(*chunks: str, limit: int = 24000, ellipsis: str = "\n\n...[TRUNCATED]") -> str:
    merged = "\n\n".join([c.strip() for c in (chunks or []) if c and c.strip()])
    if not merged:
        return ""
    if len(merged) <= limit:
        return merged
    return (merged[:limit] + ellipsis)

# =========================
# 자동 분할(이력서/자소서)
# =========================
# 힌트 단어(ko/en) 보강
RESUME_HINTS = r"(이력서|경력사항|경력기술서|프로젝트|보유기술|자격증|수상|Work\s*Experience|Career|Resume|Projects?)"
COVER_HINTS  = r"(자기소개서|자소서|지원동기|성장과정|입사 후 포부|Cover\s*Letter|Essay|에세이|문항\s*\d+)"

_MIN_SEG_LEN = 150  # 너무 짧은 조각은 오탐으로 간주

def auto_split_resume_cover(name: str, text: str) -> Dict[str, str]:
    """
    문서 하나에서 이력서/자소서를 힌트 기반으로 분할(간단 규칙)
    - 불확실하면 빈 dict 반환 (오탐 방지)
    """
    if not text or len(text) < 200:
        return {}
    t = text
    resume_like = re.search(RESUME_HINTS, t, flags=re.I)
    cover_like  = re.search(COVER_HINTS,  t, flags=re.I)
    out: Dict[str, str] = {}
    if resume_like and cover_like:
        a, b = resume_like.start(), cover_like.start()
        if abs(a - b) < 20:
            # 매우 근접 → 불확실
            return {}
        if a < b:
            r = t[a:b].strip()
            c = t[b:].strip()
            if len(r) >= _MIN_SEG_LEN and len(c) >= _MIN_SEG_LEN:
                out["이력서(추정)"] = _normalize_text(r)
                out["자기소개서(추정)"] = _normalize_text(c)
        else:
            c = t[b:a].strip()
            r = t[a:].strip()
            if len(r) >= _MIN_SEG_LEN and len(c) >= _MIN_SEG_LEN:
                out["자기소개서(추정)"] = _normalize_text(c)
                out["이력서(추정)"] = _normalize_text(r)
    elif resume_like:
        if len(t.strip()) >= 400:
            out["이력서(추정)"] = _normalize_text(t)
    elif cover_like:
        if len(t.strip()) >= 400:
            out["자기소개서(추정)"] = _normalize_text(t)
    # 여기서도 비정상적으로 짧으면 반환 취소
    for k in list(out.keys()):
        if len(out[k]) < _MIN_SEG_LEN:
            out.pop(k, None)
    if len(out) == 1:
        # 단일 추정은 신뢰도 낮으니 상층에서 실제 파일 존재 여부와 함께 사용 권장
        return out
    return out

# 선택) 메타 포함 버전: 신뢰도/근거 위치까지 제공 (서비스에서 필요할 때 사용)
def auto_split_with_meta(name: str, text: str) -> Tuple[Dict[str, str], Dict[str, Any]]:
    parts = auto_split_resume_cover(name, text)
    meta: Dict[str, Any] = {"confidence": 0.0, "reason": "", "keys": list(parts.keys())}
    if not parts:
        meta["reason"] = "힌트 미검출 또는 길이 부족/오탐"
        return parts, meta
    if len(parts) == 2:
        meta["confidence"] = 0.9
        meta["reason"] = "이력서/자소서 힌트 모두 검출, 길이 기준 통과"
    else:
        meta["confidence"] = 0.6
        meta["reason"] = "단일 섹션 추정 (실제 파일/입력과 병합 권장)"
    return parts, meta
