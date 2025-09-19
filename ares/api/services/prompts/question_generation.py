# ares/api/services/prompts/question_generation.py
"""
Prompts for generating various types of questions (follow-up, icebreaker, etc.).
"""
from .base import SYSTEM_RULES, prompt_json_output_only

# -----------------------------------------------------------------------------
# 꼬리 질문 생성 (Follow-up) — 레거시 단일 문장형
# -----------------------------------------------------------------------------
prompt_rag_follow_up_question = (
    SYSTEM_RULES
    + """
{persona_description}
현재 단계: [{stage}], 목표: [{objective}]
직전 답변 결핍 힌트: {deficit_hint}
원 질문의 목표 달성을 위해 논리를 더 파고들거나 부족한 부분을 보완하는 핵심 꼬리 질문 1개를 생성하세요(한 문장, ≤200자).
[출력]
{ "follow_up_question": "..." }
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# V2: 꼬리질문 (키워드 기반 + 폴백 + 근거)
# -----------------------------------------------------------------------------
prompt_followup_v2 = (
    SYSTEM_RULES
    + """
{persona_description}
목표: '직전 답변(latest_answer)'의 핵심 키워드를 추출하고, 해당 키워드를 근거로 "메인 질문 1개 + 꼬리질문 1~3개"를 생성합니다.
키워드가 부실하면 일반 목적의 안전한 꼬리질문으로 폴백합니다.

[입력]
- phase: {phase}                # "intro" | "core" | "wrapup"
- question_type: {question_type}# "icebreaking|self_intro|motivation|star|competency|case|system|hard|wrapup"
- objective: {objective}
- latest_answer: {latest_answer}
- company_context: {company_context}
- ncs: {ncs}
- kpi: {kpi}

[출력 스키마]
{
  "question": "메인 질문 1개(≤200자)",
  "followups": ["꼬리1","꼬리2"],
  "rationale": "키워드 기반 혹은 폴백 사유(200자 이내)",
  "fallback_used": false,
  "keywords": ["키워드1","키워드2"]
}
규칙:
- followups는 1~3개
- latest_answer가 빈약하여 의미 있는 키워드를 못 찾으면 fallback_used=true로 표기하고, 안전한 일반 꼬리질문을 생성
- 민감/사생활/차별 유발 소재 금지
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 신규: 아이스브레이킹/자기소개/지원동기 (JSON 스키마 + 제약)
# -----------------------------------------------------------------------------
prompt_icebreaker_question = (
    SYSTEM_RULES
    + """
면접을 시작하기 전, 지원자의 긴장을 풀어주기 위한 아이스브레이킹 질문을 생성합니다.

**규칙:**
1.  반드시 "안녕하세요. 면접에 오신 것을 환영합니다." 와 같은 따뜻한 인사말로 시작해야 합니다.
2.  인사말에 이어서, 분위기를 편안하게 만드는 가벼운 질문을 **하나만** 추가하세요.
3.  **질문 주제:** 지원자를 배려하고 긴장을 풀어주는 상황적 질문을 우선적으로 생성합니다.
    - **좋은 예시:** "오시느라 고생하셨습니다. 혹시 뭐 타고 오셨나요?", "여기까지 오시는 데 얼마나 걸리셨어요?", "긴장되실 텐데, 물 한잔 드시고 편하게 시작하시겠어요?"
    - **지양할 예시:** 개인적인 취미, 최근 본 영화, 주말 계획 등 사적인 경험에 대한 질문.
4.  **제약 조건:**
    - 전체 내용은 2~3문장, 150자 이내로 간결해야 합니다.
    - 민감 정보(가족, 건강, 정치/종교 등)는 절대 묻지 않습니다.

**출력 형식:**
{ "question": "[인사말] [아이스브레이킹 질문]" }
"""
    + prompt_json_output_only
)

prompt_self_introduction_question = (
    SYSTEM_RULES
    + """
지원자의 자기소개를 유도하는 질문을 한국어로 정확히 1개만 생성하세요.
제약:
- 1문장, 60자 이내, 공손하고 간결
- 예시 표현(예: "1분 자기소개")을 포함해도 되지만 강제하지는 말 것
[출력]
{ "question": "..." }
"""
    + prompt_json_output_only
)

prompt_motivation_question = (
    SYSTEM_RULES
    + """
[컨텍스트]
- 회사명: {company_name}
- 직무명: {job_title}

[요청]
위 컨텍스트를 활용하여, 지원 동기를 묻는 '의문문'을 한국어로 정확히 1개만 생성하세요. 반드시 물음표(?)로 끝나야 합니다.

[제약]
- 1문장, 70자 이내, 공손하고 간결
- 예시: "우리 회사에 지원하신 동기는 무엇인가요?", "{company_name}의 {job_title} 직무에 관심을 갖게 된 계기가 있으신가요?"

[출력]
{ "question": "..." }
"""
    + prompt_json_output_only
)

# --- prompt_soft_followups (아이스브레이크/자기소개/지원동기 전용) ---
prompt_soft_followup = (
    SYSTEM_RULES
    + """
다음 답변에 이어서 아주 가벼운 꼬리질문 1개만 한국어로 생성하세요.
목표는 대화를 자연스럽게 잇거나(icebreak), 핵심을 조금만 구체화(intro/motivation)하는 것입니다.
제약:
- 1문장, 80자 이내, 공손하고 간결
- 사생활 침해(가족/건강/정치·종교/재정/연애) 금지, 압박 금지
- 이미 답한 내용의 단순 반복/동의 유도 금지
- 상황 맥락을 부드럽게 반영

[컨텍스트]
- stage: {stage}               # "icebreak" | "intro:self" | "intro:motivation"
- company: {company_name}
- role: {job_title}
- persona: {persona_description}

[원 질문]
{origin_question}

[직전 답변]
{user_answer}

[부족 힌트]
{deficit_hint}

[출력]
{ "follow_up_question": "..." }
"""
    + prompt_json_output_only
)
