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
{{ "follow_up_question": "..." }}
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
목표: '직전 답변 분석 결과(analysis_summary)'와 '평가 기준(evaluation_criteria)'을 종합하여 지원자의 역량을 검증하거나 주장의 근거를 확인하는 "꼬리질문 1~2개"를 생성합니다.
**[중요] '평가 기준'에 명시된 Rubric과 기대 답변(Expected Points)을 최우선으로 고려하여, 답변에서 누락되었거나 부족했던 점을 파고드는 질문을 생성해야 합니다.**

[입력]
- phase: {phase}                # "intro" | "core" | "wrapup"
- question_type: {question_type}# "icebreaking|self_intro|motivation|star|competency|case|system|hard|wrapup"
- objective: {objective}
- latest_answer: {latest_answer}
- analysis_summary: {analysis_summary} # 답변 분석 요약 (피드백, 강점, 약점 등)
- evaluation_criteria: {evaluation_criteria} # Rubric 및 기대 답변 포인트
- company_context: {company_context}
- ncs: {ncs}
- kpi: {kpi}

[출력 스키마]
{{
  "followups": ["꼬리질문1","꼬리질문2"],
  "rationale": "무엇을 검증하기 위한 질문인지에 대한 근거(200자 이내)",
  "fallback_used": false,
  "keywords": ["답변의 핵심 키워드1","키워드2"]
}}
규칙:
- **[근거 요구 특별 규칙]** 만약 지원자의 답변이 구체적인 경험이나 근거 없이 자신감, 포부, 의견만을 주장하는 형태라면(예: "제가 최고입니다", "잘 할 수 있습니다", "열심히 하겠습니다"), 다른 어떤 질문보다 주장에 대한 구체적인 근거, 이유, 또는 관련 경험을 요구하는 질문을 최우선으로 생성해야 합니다.
- **[자기소개 특별 규칙]** `question_type`이 "self_intro"인 경우, `latest_answer`에서 언급된 구체적인 경험(예: 특정 프로젝트, 근무 기간, 기술)을 직접적으로 인용하여 더 자세한 설명을 요구하는 질문을 생성하세요. (예: "네, 자기소개 잘 들었습니다. ...에서 3년간 근무하셨다고 하셨는데, 그 경험에 대해 더 자세히 말씀해주시겠어요?")
- followups는 1~2개로 제한합니다.
- evaluation_criteria와 analysis_summary를 최우선으로 활용하여 질문을 생성하세요.
- latest_answer가 빈약하여 의미 있는 질문 생성이 어려우면 fallback_used=true로 표기하고, 안전한 일반 꼬리질문을 생성하세요.
- 민감/사생활/차별 유발 소재는 절대 금지입니다.
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 신규: 아이스브레이킹/자기소개/지원동기 (JSON 스키마 + 제약)
# -----------------------------------------------------------------------------
prompt_icebreaker_question = (
    SYSTEM_RULES
    + """
면접 시작 전, 지원자의 긴장을 풀어주기 위한 아이스브레이킹 질문 **하나만** 생성합니다.

**규칙:**
1.  **인사말은 절대 포함하지 마세요.** 오직 질문 자체만 생성해야 합니다.
2.  분위기를 편안하게 만드는 가벼운 질문이어야 합니다.
3.  **질문 주제:** 지원자를 배려하고 긴장을 풀어주는 상황적 질문을 우선적으로 생성합니다.
    - **좋은 예시:** "오늘 여기까지 오시는 데 얼마나 걸리셨어요?", "오시느라 고생하셨습니다. 혹시 뭐 타고 오셨나요?", "긴장되실 텐데, 물 한잔 드시고 편하게 시작하시겠어요?"
    - **지양할 예시:** 개인적인 취미, 최근 본 영화, 주말 계획 등 사적인 경험에 대한 질문.
4.  **제약 조건:**
    - 전체 내용은 1문장, 80자 이내로 간결해야 합니다.
    - 민감 정보(가족, 건강, 정치/종교 등)는 절대 묻지 않습니다.

**출력 형식:**
{{ "question": "[아이스브레이킹 질문]" }}
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
{{ "question": "..." }}
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
{{ "question": "..." }}
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
- stage: {stage}               # "icebreak" | "intro:self" | "intro:motivation" | "intro:combined"
- company: {company_name}
- role: {job_title}
- persona: {persona_description}

[분석 규칙]
- stage가 "intro:combined"인 경우: 답변에 자기소개(강점/역량)와 지원동기(회사/직무 관심)가 모두 포함되었는지 확인. 둘 중 부족한 요소 하나만 가볍게 구체화하도록 유도. 둘 다 충분하면 "네, 잘 들었습니다." 와 같은 간단한 전환 문구 생성.

[원 질문]
{origin_question}

[직전 답변]
{user_answer}

[부족 힌트]
{deficit_hint}

[출력]
{{ "follow_up_question": "..." }}
"""
    + prompt_json_output_only
)
