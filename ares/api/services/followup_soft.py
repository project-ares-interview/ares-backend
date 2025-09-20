# ares/api/services/followup_soft.py
from __future__ import annotations
from typing import Callable, Optional
import re, random

from ares.api.services.prompts import prompt_soft_followup

SOFT_FU_MAX_PER_TURN = 1
MIN_LEN_ICEBREAK = 25
MIN_LEN_INTRO = 40
MIN_LEN_MOTIVE = 40

ICEBREAK_TEMPLATES_FU = [
    "오늘 자리나 연결 상태는 불편한 점 없으세요?",
    "시작 전에 호흡 한번 고르고 편하게 말씀해볼까요?"
]
INTRO_TEMPLATES_FU = [
    "방금 소개 중 핵심 강점을 한두 가지로 꼽으면 무엇일까요?",
    "최근 경험 중 지원 직무와 가장 맞닿은 사례를 하나만 짧게 말씀해 주실 수 있을까요?"
]
MOTIVE_TEMPLATES_FU = [
    "우리 {company}의 어떤 점이 특히 끌렸는지 한 가지 더 말씀해 주실 수 있을까요?",
    "{role} 역할에서 본인이 가장 빠르게 기여할 수 있는 영역을 무엇으로 보시나요?"
]

def _deficit_hint(turn_type: str, answer: str) -> str:
    L = len(answer or "")
    if turn_type == "icebreak":
        return "답변이 매우 짧으면 친절한 확인/안심 멘트를 권장."
    if turn_type == "intro:self":
        return "핵심 강점/최근 사례/역할 키워드가 부족하면 가벼운 구체화 유도."
    if turn_type == "intro:motivation":
        return "회사/직무 특정 포인트(제품/문화/문제영역) 언급이 없으면 한 가지 보완 유도."
    if turn_type == "intro:combined":
        return "답변에 자기소개(강점)와 지원동기(회사/직무 관심) 중 부족한 내용이 있다면, 해당 부분을 구체화하도록 유도하는 질문을 생성하세요."
    return ""

def _too_short(turn_type: str, answer: str) -> bool:
    n = len((answer or "").strip())
    if turn_type == "icebreak": return n < MIN_LEN_ICEBREAK
    if turn_type == "intro:self": return n < MIN_LEN_INTRO
    if turn_type == "intro:motivation": return n < MIN_LEN_MOTIVE
    if turn_type == "intro:combined": return n < MIN_LEN_INTRO  # 자기소개 최소 길이를 재사용
    return False

def _template_pool(turn_type: str, company: str, role: str):
    if turn_type == "icebreak":
        return ICEBREAK_TEMPLATES_FU
    if turn_type == "intro:self":
        return INTRO_TEMPLATES_FU
    if turn_type == "intro:motivation":
        return [t.format(company=company, role=role) for t in MOTIVE_TEMPLATES_FU]
    return []

def make_soft_followup(
    *,
    llm_call_json: Callable[[str], dict],   # prompt 문자열 -> dict(JSON) 반환
    turn_type: str,                          # "icebreak" | "intro:self" | "intro:motivation"
    origin_question: str,
    user_answer: str,
    company_name: str = "",
    job_title: str = "",
    persona_description: str = "공손하고 편안한 톤",
    force: bool = False                      # True면 길이와 무관하게 1개 생성
) -> Optional[str]:
    # 생성 필요 판단
    if not force and not _too_short(turn_type, user_answer):
        return None

    # 템플릿 1차 후보
    pool = _template_pool(turn_type, company_name, job_title)
    candidate = random.choice(pool) if pool else None

    # LLM 시도
    try:
        prompt = prompt_soft_followup.format(
            stage=turn_type,
            company_name=company_name,
            job_title=job_title,
            persona_description=persona_description,
            origin_question=origin_question,
            user_answer=user_answer,
            deficit_hint=_deficit_hint(turn_type, user_answer),
        )
        out = llm_call_json(prompt)
        fu = (out or {}).get("follow_up_question","").strip()
        # 간단한 검증
        if fu and len(fu) <= 80 and not re.search(r"[!?]{3,}|[\u263a-\U0001f9ff]", fu):
            return fu
    except Exception:
        pass

    return candidate
