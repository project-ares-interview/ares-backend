"""
파일 처리 유틸
- TXT, DOCX, PDF/이미지 → 텍스트 추출
- 가상 문서(붙여넣기 텍스트) 관리
- 이력서/자소서 자동 분리
"""

from __future__ import annotations
import os, re
import docx
from typing import Dict, List, Tuple
from ares.api.services.ocr_service import di_analyze_file

# --- 파일 읽기 ---
def read_docx(fp: str) -> str:
    try:
        d = docx.Document(fp)
        return "\n".join(p.text for p in d.paragraphs)
    except Exception as e:
        print("[DOCX 읽기 오류]", e)
        return ""

def read_txt(fp: str) -> str:
    try:
        return open(fp, "r", encoding="utf-8", errors="ignore").read()
    except Exception as e:
        print("[TXT 읽기 오류]", e)
        return ""

def extract_text_from_path(path: str) -> str:
    low = (path or "").lower()
    if low.endswith((".pdf", ".png", ".jpg", ".jpeg")):
        return di_analyze_file(path)
    if low.endswith(".docx"):
        return read_docx(path)
    if low.endswith(".txt"):
        return read_txt(path)
    return ""

def collect_context(files: List[str]) -> Tuple[str, Dict[str, str]]:
    """
    업로드된 파일들에서 텍스트 추출
    반환: (전체 텍스트 조합, {basename: text})
    """
    full: list[str] = []
    per_file: Dict[str, str] = {}
    for f in files or []:
        text = extract_text_from_path(f)
        if text:
            full.append(f"\n\n# [{os.path.basename(f)}]\n{text}")
            per_file[os.path.basename(f)] = text
    return ("\n".join(full).strip(), per_file)

def virtual_append(per_file: Dict[str, str], virt_name: str, text: str):
    """
    붙여넣기 텍스트를 '가상 파일'처럼 map에 추가
    """
    if text and text.strip():
        per_file[virt_name] = text.strip()

def join_texts(*chunks: str, limit: int = 20000) -> str:
    """
    여러 텍스트 합치기 (길이 제한 적용)
    """
    merged = "\n\n".join([c for c in chunks if c and c.strip()])
    return merged[:limit]

# --- 자동 분할(이력서/자소서) ---
RESUME_HINTS = r"(이력서|경력사항|Work\s*Experience|경력기술서|프로젝트|Career|Resume)"
COVER_HINTS  = r"(자기소개서|자소서|지원동기|성장과정|Cover\s*Letter|에세이|Essay|문항\s*\d+)"

def auto_split_resume_cover(name: str, text: str) -> Dict[str, str]:
    """
    한 문서 안에 이력서/자소서가 섞여 있을 경우 섹션 분리 → 가상 문서 dict 생성
    반환 예시:
      {
        '지원서.pdf#이력서': '...',
        '지원서.pdf#자소서': '...'
      }
    """
    if not text or len(text) < 200:
        return {}
    blocks = re.split(r"\n(?=#+\s|\d+\.\s|[-=]{5,}|^\s*$)", text)
    resume_parts, cover_parts = [], []
    for b in blocks:
        if re.search(RESUME_HINTS, b, re.I):
            resume_parts.append(b.strip())
        if re.search(COVER_HINTS, b, re.I):
            cover_parts.append(b.strip())
    out: Dict[str, str] = {}
    if resume_parts and cover_parts:
        out[f"{name}#이력서"] = "\n\n".join(resume_parts)[:12000]
        out[f"{name}#자소서"] = "\n\n".join(cover_parts)[:12000]
    return out
