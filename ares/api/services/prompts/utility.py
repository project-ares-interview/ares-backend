# ares/api/services/prompts/utility.py
"""
Utility prompts and helper functions.
"""
from __future__ import annotations
from typing import Callable, Dict, Any
import random

from .question_generation import (
    prompt_icebreaker_question,
    prompt_self_introduction_question,
    prompt_motivation_question,
)

# -----------------------------------------------------------------------------
# JSON 교정 프롬프트 (파싱 실패 시 재시도)
# -----------------------------------------------------------------------------
prompt_rag_json_correction = (
    "The previous output did not parse as JSON. Return ONLY a JSON object. "
    "Do not include code fences, markdown, or any explanation. Fix any missing commas or quotes. "
    'If a required field is missing, add it with an empty string "" or empty array [] according to the schema.'
)

# -----------------------------------------------------------------------------
# 오케스트레이션(체이닝) 참고 문서
# -----------------------------------------------------------------------------
ORCHESTRATION_DOC = """
[Orchestration Flow — Structural Interview RAG]
사용자 답변 입력(incoming answer)
-> prompt_identifier 실행
결과.frameworks에 "STAR" 포함 시: 다음 단계에 STAR 우선(여러 개면 우선순위 규칙 적용)
선택된 프레-임워크에 대해 prompt_extractor 실행 (component_list는 해당 프레임워크 기본요소 키 배열)
-> prompt_scorer 실행 (persona, role, retrieved_ncs_details, framework_name 주입)
-> prompt_score_explainer 실행 (scorer 출력 사용)
-> prompt_coach 실행 (scoring_reason + user_answer + NCS)
(선택) prompt_model_answer 실행 (코칭 반영 모범답안)
RAG 기반 평가가 필요한 턴에서는:
-> prompt_rag_answer_analysis → claims_checked/analysis/feedback
사용자에게 보여주기 전 마지막 단계:
-> prompt_bias_checker(any_text=피드백/리포트/해설 등)
세션 종료 시:
-> (레거시) prompt_rag_final_report
-> (추천) prompt_detailed_section 배치 → prompt_detailed_overview 종합
"""

# -----------------------------------------------------------------------------
# 캐싱 전략 — 키/TTL 가이드
# -----------------------------------------------------------------------------
CACHE_KEYS = {
    # 정적/반정적 컨텍스트
    "JD_ANALYSIS": "jd:{jd_hash}",                 # 동일 JD 재사용
    "NCS_SUMMARY": "ncs:{role}:{version}",        # role별 NCS 요약(버전 태깅)
    "BUSINESS_INFO": "biz:{company}:{yymm}",      # 회사/기간별 사업 요약
    # 동적 결과(짧은 TTL)
    "INTERVIEW_PLAN": "plan:{mode}:{jd_hash}:{resume_hash}",
    "RAG_WEB": "rag:web:{query_hash}",
}
CACHE_TTLS = {
    "JD_ANALYSIS": 60 * 60 * 12,      # 12h
    "NCS_SUMMARY": 60 * 60 * 24 * 7,  # 7d
    "BUSINESS_INFO": 60 * 60 * 24,    # 1d
    "INTERVIEW_PLAN": 60 * 30,        # 30m
    "RAG_WEB": 60 * 10,               # 10m
}

# -----------------------------------------------------------------------------
# 안전/저비용 템플릿 + LLM 폴백 헬퍼 (운영 안정화)
# -----------------------------------------------------------------------------
ICEBREAK_TEMPLATES_KO = [
    # --- 이동/장소 관련 (가장 보편적) ---
    "오늘 면접 장소까지 오시는 길은 편안하셨나요?",
    "여기까지 오시는데 어려움은 없으셨는지요?",
    
    # --- 온라인/원격 면접 ---
    "면접을 시작하기 전에, 제 목소리는 잘 들리고 화면도 선명하게 보이시나요?",
    "온라인 환경이 괜찮으신지 먼저 확인하고 시작하겠습니다. 연결 상태는 안정적이신가요?",
    
    # --- 컨디션/분위기 조성 ---
    "오늘 컨디션은 어떠신가요? 긴장되시면 편하게 말씀해주세요.",
    "시작하기 전에 물 한잔하시겠어요? 편안한 상태에서 시작하셨으면 합니다.",
    "면접은 편안한 대화처럼 진행될 예정이니, 긴장하지 않으셔도 괜찮습니다.",
    
    # --- 대화 유도/관심사 (자연스러운 시작) ---
    "긴장을 푸는 의미에서, 최근 관심있게 본 기술 트렌드나 업계 뉴스가 있다면 간단히 말씀해주시겠어요?",
    "최근에 읽은 책이나 인상 깊게 본 콘텐츠가 있다면, 잠시 이야기해주실 수 있나요?",
    
    # --- 면접으로의 부드러운 연결 ---
    "본격적인 질문에 앞서, 오늘 면접에서 '이것만큼은 꼭 보여주고 싶다' 하는 점이 있다면 먼저 들어볼 수 있을까요?",
]
INTRO_TEMPLATE_KO = "간단히 자기소개 부탁드립니다."
MOTIVE_TEMPLATE_KO = "이번 직무에 지원하신 동기를 말씀해 주세요."

WRAPUP_TEMPLATES_KO = [
    "마지막으로 질문하고 싶은 것이 있으신가요?",
    "마지막으로 하고 싶은 말이 있으신가요?"
]

# llm_call: (prompt_str: str) -> Dict[str, Any] 를 기대 (JSON 파싱 실패 시 예외 권장)
def make_icebreak_question_llm_or_template(llm_call: Callable[[str], Dict[str, Any]]) -> str:
     try:
         # AI를 호출하여 동적으로 아이스브레이킹 질문 생성
         out = llm_call(prompt_icebreaker_question)
         q = (out or {}).get("question", "").strip()
         
         # AI가 생성에 성공하면, 생성된 질문과 안전 템플릿을 합쳐서 그 중 하나를 무작위로 선택
         if q:
             combined_pool = ICEBREAK_TEMPLATES_KO + [q]
             return random.choice(combined_pool)
         
         # AI가 생성에 실패하거나 빈 문자열을 반환하면 안전한 템플릿만 사용
         return random.choice(ICEBREAK_TEMPLATES_KO)
     except Exception:
         # LLM 호출 중 에러 발생 시 안전하게 템플릿으로 폴백
         return random.choice(ICEBREAK_TEMPLATES_KO)

def make_intro_question_llm_or_template(llm_call: Callable[[str], Dict[str, Any]]) -> str:
    try:
        out = llm_call(prompt_self_introduction_question)
        q = (out or {}).get("question", "").strip()
        return q or INTRO_TEMPLATE_KO
    except Exception:
        return INTRO_TEMPLATE_KO

def make_motive_question_llm_or_template(llm_call: Callable[[str], Dict[str, Any]]) -> str:
    try:
        out = llm_call(prompt_motivation_question)
        q = (out or {}).get("question", "").strip()
        return q or MOTIVE_TEMPLATE_KO
    except Exception:
        return MOTIVE_TEMPLATE_KO

def make_wrapup_question_template() -> str:
    return random.choice(WRAPUP_TEMPLATES_KO)
