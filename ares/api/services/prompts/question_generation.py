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
목표: '직전 답변 분석 결과(analysis_summary)'와 '평가 기준(evaluation_criteria)', 그리고 '[대화 이력]'을 종합하여 지원자의 역량을 검증하거나 주장의 근거를 확인하는 "꼬리질문 1~2개"를 생성합니다.

[규칙]
1.  **출력 형식:** 반드시 아래 JSON 스키마와 [SSML 출력 골격 예시]를 따라야 합니다.
2.  **SSML 생성 상세 규칙 (매우 중요):** 실제 면접관 화법을 재현하기 위해 아래 규칙을 반드시 지키세요.
    - **자연스러운 도입부:** 본 질문 앞에 짧은 도입 구절(예: “네, 이력서를 보니…”, “좋습니다. 그럼…”)을 넣고, 이 부분만 약간 빠르게 말합니다. `<prosody rate="+5%">도입 구절</prosody>`
    - **전략적 끊어 읽기:** 핵심 프로젝트명·기술 용어 **바로 앞**에 `<break time="200ms"/>` 또는 `300ms`를 1회 삽입합니다.
    - **핵심 단어 강조:** ‘구체적인 역할’, ‘기여한 부분’, ‘가장 어려웠던 점’ 같이 **단 1~2개 키워드만** `<emphasis level="moderate">`로 강조합니다.
    - **어조의 변화:** 이력서의 특정 고유명사(프로젝트명, 성과 수치)를 말할 때 `<prosody pitch="+5%">`로 **미세하게** 높입니다.
    - **문장 구조:** `<speak>…</speak>` 최상위 구조를 지키고, 본문은 `<p>`/`<s>`로 나눕니다. 불필요한 태그 중첩 금지.
    - **발음 보정(선택):** 약어·영문 용어는 `<sub alias="한국어 발음">EPCM</sub>`처럼 처리합니다.
    - **금지 사항:** 과도한 `prosody` 중첩, 300ms 초과 연속 `break`, 3개 초과 `emphasis`, 미닫힘 태그 금지.
3.  **꼬리질문 스타일 규칙:** 꼬리질문은 대화의 연장선입니다. 절대 "안녕하세요", "네, 잘 들었습니다"와 같은 인사말이나 서두로 시작하지 마세요. 즉시 질문의 본론으로 들어가야 합니다.
4.  **일관성 검증 최우선 규칙:** 'transcript_context'와 'latest_answer' 사이에 명백한 모순이 발견되면, 다른 모든 규칙을 무시하고 해당 모순을 해결하기 위한 질문을 생성하세요.
5.  **근거 요구 특별 규칙:** 만약 지원자의 답변이 구체적인 경험이나 근거 없이 자신감, 포부, 의견만을 주장하는 형태라면, 주장에 대한 구체적인 근거, 이유, 또는 관련 경험을 요구하는 질문을 최우선으로 생성해야 합니다.
6.  **자기소개 특별 규칙:** `question_type`이 "self_intro"인 경우, `latest_answer`에서 언급된 구체적인 경험을 직접적으로 인용하여 더 자세한 설명을 요구하는 질문을 생성하세요.

[입력]
- phase: {phase}
- question_type: {question_type}
- objective: {objective}
- transcript_context: {transcript_context}
- latest_answer: {latest_answer}
- analysis_summary: {analysis_summary}
- evaluation_criteria: {evaluation_criteria}
- company_context: {company_context}
- ncs: {ncs}
- kpi: {kpi}

[SSML 출력 골격 예시]
<speak xmlns="http://www.w3.org/2001/10/synthesis">
   <p><prosody rate="+5%">네, 그 부분에 대해서…</prosody></p>
   <p>
     <s>조금 더 자세히 설명해주시겠어요?</s>
     <s>예를 들어, <emphasis level="moderate">어떤 어려움</emphasis>이 있었나요?</s>
   </p>
</speak>

[출력 스키마]
{{
  "followups": [
    {{
      "text": "일반 텍스트 꼬리질문",
      "ssml": "<speak>...</speak>"
    }}
  ],
  "rationale": "무엇을 검증하기 위한 질문인지에 대한 근거(200자 이내)",
  "fallback_used": false,
  "keywords": ["답변의 핵심 키워드1","키워드2"]
}}
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
1.  **출력 형식:** 반드시 아래 JSON 스키마와 [SSML 출력 골격 예시]를 따라야 합니다.
2.  **SSML 생성 상세 규칙 (매우 중요):** 실제 면접관 화법을 재현하기 위해 아래 규칙을 반드시 지키세요.
    - **자연스러운 도입부:** 본 질문 앞에 짧은 도입 구절(예: “네, 이력서를 보니…”, “좋습니다. 그럼…”)을 넣고, 이 부분만 약간 빠르게 말합니다. `<prosody rate="+5%">도입 구절</prosody>`
    - **전략적 끊어 읽기:** 핵심 프로젝트명·기술 용어 **바로 앞**에 `<break time="200ms"/>` 또는 `300ms`를 1회 삽입합니다.
    - **핵심 단어 강조:** ‘구체적인 역할’, ‘기여한 부분’, ‘가장 어려웠던 점’ 같이 **단 1~2개 키워드만** `<emphasis level="moderate">`로 강조합니다.
    - **어조의 변화:** 이력서의 특정 고유명사(프로젝트명, 성과 수치)를 말할 때 `<prosody pitch="+5%">`로 **미세하게** 높입니다.
    - **문장 구조:** `<speak>…</speak>` 최상위 구조를 지키고, 본문은 `<p>`/`<s>`로 나눕니다. 불필요한 태그 중첩 금지.
    - **발음 보정(선택):** 약어·영문 용어는 `<sub alias="한국어 발음">EPCM</sub>`처럼 처리합니다.
    - **금지 사항:** 과도한 `prosody` 중첩, 300ms 초과 연속 `break`, 3개 초과 `emphasis`, 미닫힘 태그 금지.
3.  **인사말 금지:** 질문에 "안녕하세요" 같은 인사말을 절대 포함하지 마세요.
4.  **질문 주제:** 지원자를 배려하고 긴장을 풀어주는 상황적 질문을 우선적으로 생성합니다.
5.  **제약 조건:** 1문장, 80자 이내로 간결해야 합니다.

[SSML 출력 골격 예시]
<speak xmlns="http://www.w3.org/2001/10/synthesis">
   <p>
     <s>오늘 여기까지 오시는 데<break time="200ms"/> 어려움은 없으셨나요?</s>
   </p>
</speak>

[출력 형식]
{{
  "question": {{
    "text": "일반 텍스트 질문",
    "ssml": "<speak>SSML 버전 질문</speak>"
  }}
}}
"""
    + prompt_json_output_only
)

prompt_self_introduction_question = (
    SYSTEM_RULES
    + """
지원자의 자기소개를 유도하는 질문을 한국어로 정확히 1개만 생성하세요.

**규칙:**
1.  **출력 형식:** 반드시 아래 JSON 스키마와 [SSML 출력 골격 예시]를 따라야 합니다.
2.  **SSML 생성 상세 규칙 (매우 중요):** 실제 면접관 화법을 재현하기 위해 아래 규칙을 반드시 지키세요.
    - **자연스러운 도입부:** 본 질문 앞에 짧은 도입 구절(예: “네, 이력서를 보니…”, “좋습니다. 그럼…”)을 넣고, 이 부분만 약간 빠르게 말합니다. `<prosody rate="+5%">도입 구절</prosody>`
    - **전략적 끊어 읽기:** 핵심 프로젝트명·기술 용어 **바로 앞**에 `<break time="200ms"/>` 또는 `300ms`를 1회 삽입합니다.
    - **핵심 단어 강조:** ‘구체적인 역할’, ‘기여한 부분’, ‘가장 어려웠던 점’ 같이 **단 1~2개 키워드만** `<emphasis level="moderate">`로 강조합니다.
    - **어조의 변화:** 이력서의 특정 고유명사(프로젝트명, 성과 수치)를 말할 때 `<prosody pitch="+5%">`로 **미세하게** 높입니다.
    - **문장 구조:** `<speak>…</speak>` 최상위 구조를 지키고, 본문은 `<p>`/`<s>`로 나눕니다. 불필요한 태그 중첩 금지.
    - **발음 보정(선택):** 약어·영문 용어는 `<sub alias="한국어 발음">EPCM</sub>`처럼 처리합니다.
    - **금지 사항:** 과도한 `prosody` 중첩, 300ms 초과 연속 `break`, 3개 초과 `emphasis`, 미닫힘 태그 금지.
3.  **제약 조건:** 1문장, 60자 이내, 공손하고 간결.

[SSML 출력 골격 예시]
<speak xmlns="http://www.w3.org/2001/10/synthesis">
   <p>
     <s><prosody rate="+5%">네, 좋습니다.</prosody> <break time="300ms"/>먼저, 준비하신 자기소개를 부탁드립니다.</s>
   </p>
</speak>

[출력 형식]
{{
  "question": {{
    "text": "일반 텍스트 질문",
    "ssml": "<speak>SSML 버전 질문</speak>"
  }}
}}
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
위 컨텍스트를 활용하여, 지원 동기를 묻는 '의문문'을 한국어로 정확히 1개만 생성하세요.

**규칙:**
1.  **출력 형식:** 반드시 아래 JSON 스키마와 [SSML 출력 골격 예시]를 따라야 합니다.
2.  **SSML 생성 상세 규칙 (매우 중요):** 실제 면접관 화법을 재현하기 위해 아래 규칙을 반드시 지키세요.
    - **자연스러운 도입부:** 본 질문 앞에 짧은 도입 구절(예: “네, 이력서를 보니…”, “좋습니다. 그럼…”)을 넣고, 이 부분만 약간 빠르게 말합니다. `<prosody rate="+5%">도입 구절</prosody>`
    - **전략적 끊어 읽기:** 핵심 프로젝트명·기술 용어 **바로 앞**에 `<break time="200ms"/>` 또는 `300ms`를 1회 삽입합니다.
    - **핵심 단어 강조:** ‘구체적인 역할’, ‘기여한 부분’, ‘가장 어려웠던 점’ 같이 **단 1~2개 키워드만** `<emphasis level="moderate">`로 강조합니다.
    - **어조의 변화:** 이력서의 특정 고유명사(프로젝트명, 성과 수치)를 말할 때 `<prosody pitch="+5%">`로 **미세하게** 높입니다.
    - **문장 구조:** `<speak>…</speak>` 최상위 구조를 지키고, 본문은 `<p>`/`<s>`로 나눕니다. 불필요한 태그 중첩 금지.
    - **발음 보정(선택):** 약어·영문 용어는 `<sub alias="한국어 발음">EPCM</sub>`처럼 처리합니다.
    - **금지 사항:** 과도한 `prosody` 중첩, 300ms 초과 연속 `break`, 3개 초과 `emphasis`, 미닫힘 태그 금지.
3.  **제약 조건:** 1문장, 70자 이내, 공손하고 간결.

[SSML 출력 골격 예시]
<speak xmlns="http://www.w3.org/2001/10/synthesis">
   <p>
     <s><prosody pitch="+5%">{company_name}</prosody>의 <prosody pitch="+5%">{job_title}</prosody> 직무에 관심을 갖게 된</s>
     <s><emphasis level="moderate">특별한 계기</emphasis>가 있으신가요?</s>
   </p>
</speak>

[출력 형식]
{{
  "question": {{
    "text": "일반 텍스트 질문",
    "ssml": "<speak>SSML 버전 질문</speak>"
  }}
}}
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
