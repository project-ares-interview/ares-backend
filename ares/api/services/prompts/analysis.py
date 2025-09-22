# ares/api/services/prompts/analysis.py
"""
Prompts for analyzing candidate's answers.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

# -----------------------------------------------------------------------------
# JD 전처리기 (JD Cleaner)
# -----------------------------------------------------------------------------
prompt_jd_preprocessor = (
    SYSTEM_RULES
    + """
You are a Job Description parsing expert. Your task is to extract only the core job-related sections from the provided text.

[Extraction Sections]
- "수행 업무" (Key Responsibilities)
- "핵심 역량" (Core Competencies)
- "자격 요건" (Qualifications)
- "우대 사항" (Preferred Qualifications)
- "기술 스택" (Tech Stack)

[Rules]
- Extract text ONLY from the sections listed above.
- If a section does not exist, omit it.
- Exclude all other information such as company introduction, benefits, hiring process, contact information, legal notices, etc.
- Combine all extracted text into a single, clean block.

[Job Description Text]
{jd_text}

[Output]
(A single block of cleaned text containing only the core job requirements)
"""
)

# -----------------------------------------------------------------------------
# JD 핵심 역량 추출기 (JD Keyword Extractor)
# -----------------------------------------------------------------------------
prompt_jd_keyword_extractor = (
    SYSTEM_RULES
    + """
You are a {persona}. Your task is to analyze the provided Job Description and the company's business summary to extract the 5 to 7 most critical core competency keywords.

[Rules]
1.  **Synthesize Information**: You MUST consider both the Job Description and the Business Summary. Extract keywords that are relevant to the job AND aligned with the company's strategic direction.
2.  **Focus on Skills**: Prioritize technical skills, specific tools, and quantifiable qualifications.
3.  **Exclude Soft Skills**: Do NOT extract soft skills like 'communication', 'passion', or 'responsibility'.
4.  **Output Format**: Return ONLY a JSON object with a single key "keywords".

[Job Description]
{jd_text}

[Company Business Summary (from RAG)]
{business_summary}

[Output JSON Schema]
{{
  "keywords": ["Keyword1", "Keyword2", "Keyword3", "Keyword4", "Keyword5"]
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 기계 1: 태거 (Identifier) - 프레임워크 식별
# -----------------------------------------------------------------------------
prompt_identifier = (
    SYSTEM_RULES
    + """
당신은 사용자의 답변을 읽고 적용 가능한 프레임워크의 '이름'을 신중히 식별하는 AI입니다.
다음 중 '명시적 증거'가 있는 프레임워크만 포함: STAR, COMPETENCY, CASE, SYSTEMDESIGN.
확장요소(C/L/M)는 증거가 있을 때 접미사로 표기: 예) "STAR+C+M".
"company_values_summary"는 회사 인재상/직무 관련 핵심 가치를 100자 이내로 요약합니다.
[출력 스키마]
{{
  "frameworks": ["STAR", "CASE+M", "..."],
  "company_values_summary": "..."
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 기계 2: 요약기 (Extractor) - 프레임워크 요소 요약
# -----------------------------------------------------------------------------
prompt_extractor = (
    SYSTEM_RULES
    + """
당신은 입력 텍스트에서 [{framework_name}] 프레임워크의 구성요소에 해당하는 내용을 아래 JSON 구조에 맞게 '요약'합니다.
규칙:
- 입력 [작업 목록]은 JSON 배열(키 리스트)이며, 해당 키만 출력합니다.
- 누락된 항목은 ""로 채웁니다. 추가 키 생성 금지.
[작업 목록(JSON 배열)]
{component_list}
[출력]
{{
  "{analysis_key}": {{
    "요소1": "요약1", "요소2": "요약2", "..."
  }}
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 기계 3: 채점기 (Scorer) - main/ext 분리
# -----------------------------------------------------------------------------
prompt_scorer = (
    SYSTEM_RULES
    + """
{persona_description}
{evaluation_focus}
당신은 위 관점을 가진 채점관입니다. 아래의 [평가 기준]과 [지원자 원본 답변]을 바탕으로 [{framework_name}] 프레임워크 규칙에 따라 점수를 매기세요.
[평가 기준: 직무 역량 ({role} 직무, NCS 기반)]
{retrieved_ncs_details}
[지원자 원본 답변]
{user_answer}
[채점 가이드라인]
- 기본 요소(scores_main): 요소당 0~20점
- 확장 요소(scores_ext: challenge, learning, metrics): 요소당 0~10점
- 누락 요소는 0점
[프레임워크 요소]
STAR: situation, task, action, result
SYSTEMDESIGN: requirements, trade_offs, architecture, risks
CASE: problem, structure, analysis, recommendation
COMPETENCY: competency, behavior, impact
[출력]
{{
  "framework": "STAR|SYSTEMDESIGN|CASE|COMPETENCY",
  "scores_main": {{"요소명": 0}},
  "scores_ext": {{"challenge": 0, "learning": 0, "metrics": 0}},
  "scoring_reason": "300~600자 요약"
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 점수 해설기 (Score Explainer) — 캘리브레이션 & 개선 가이드
# -----------------------------------------------------------------------------
prompt_score_explainer = (
    SYSTEM_RULES
    + """
{persona_description}
입력 점수를 바탕으로 요소별로 왜 만점이 아닌지(why_not_max)와 개선 방법(how_to_improve)을 제시하세요.
각 요소 how_to_improve는 1~3개 체크리스트로.
[입력]
framework: {framework}
scores_main: {scores_main}
scores_ext: {scores_ext}
scoring_reason: {scoring_reason}
role: {role}
[출력]
{{
  "framework": "STAR|SYSTEMDESIGN|CASE|COMPETENCY",
  "calibration": [
    {{"element": "요소명", "given": 0, "max": 20, "gap": 20,
      "why_not_max": "...", "how_to_improve": ["...", "..."]}}
  ],
  "ext_calibration": [
    {{"element": "challenge|learning|metrics", "given": 0, "max": 10, "gap": 10,
      "why_not_max": "...", "how_to_improve": ["..."]}}
  ],
  "overall_tip": "다음 답변에서 점수를 올리기 위한 우선순위 2~3가지"
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 코치 (Coach) — 강점/개선점 현실화
# -----------------------------------------------------------------------------
prompt_coach = (
    SYSTEM_RULES
    + """
{persona_description}
아래 평가 요약과 원본 답변을 인용하여 실행 가능한 코칭을 제공합니다.
[채점관 평가 요약]
{scoring_reason}
[지원자 원본 답변]
{user_answer}
[참고(NCS)]
{retrieved_ncs_details}
[가이드라인]
- 강점/개선점 각각 3~5개(문장당 ≤120자)
- 각 항목에 반드시 원문 특정 구절 '직접 인용' 포함
- 총평 3~5문장
[출력]
{{
  "strengths": ["...인용...' ...설명", "..."],
  "improvements": ["...인용...' ...개선 제안", "..."],
  "feedback": "총평(3~5문장). {company_name}의 다음 단계 진입 조언 포함"
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 편향성 점검기 (Bias Checker) — 공정성 필터링
# -----------------------------------------------------------------------------
prompt_bias_checker = (
    SYSTEM_RULES
    + """
당신은 AI 출력물의 편향/공격/차별을 점검하는 검토자입니다.
[검토 기준 예시]
- 민감 속성(성별, 인종, 연령, 지역 등) 일반화/가정
- 장애/질병/가족상황에 대한 차별적 평가
- 폭력/모욕/인신공격
- 법적/윤리적 위험(차별적 채용 관행 암시 등)
- 과도한 확신(출처 불명 단정), 부정확한 일반화
[입력]
{any_text}
[출력]
{{
  "flagged": true,
  "issues": [
    {{"span": "...", "category": "편향|공격성|차별적 가정|민감정보 오남용|과도한 확신|기타",
      "reason": "...", "suggested_fix": "...", "severity": "low|medium|high"}}
  ],
  "sanitized_text": "문제를 수정해 공정/중립적으로 재작성한 전체 텍스트(가능하면 원문과 유사 길이 유지)"
}}
규칙:
- 문제가 전혀 없으면 flagged=false, issues=[]로 반환하고 sanitized_text에는 원문을 그대로 넣습니다.
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 모범생 (Role Model) — 모범 답안 생성
# -----------------------------------------------------------------------------
prompt_model_answer = (
    SYSTEM_RULES
    + """
{persona_description}
지원자의 아쉬운 점을 보완하여 최고 수준 모범 답안을 1개 생성하세요.
[지원자 원본 답변]
{user_answer}
[지원자 이력서]
{resume_context}
[참고(NCS)]
{retrieved_ncs_details}
[규칙]
- **[매우 중요] [지원자 이력서] 내용을 바탕으로, 질문에 가장 적합하고 이상적인 경험을 선택해야 합니다. 반드시 이력서에 있는 구체적인 프로젝트명, 회사명, 성과(숫자) 등을 직접적으로 인용하여 답변을 재구성하세요.**
- "model_answer": 400~800자
- 개선점 마커: "[추가]", "[정정]" 총 5개 이하, 동일 문장 중복 마커 금지
- "model_answer_framework": STAR|CASE|SYSTEMDESIGN|COMPETENCY 중 선택
- "selection_reason": 선택 프레임워크의 2~3개 요소 매핑 근거 간결 제시
[출력]
{{
  "model_answer": "...",
  "model_answer_framework": "...",
  "selection_reason": "..."
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 지원자 답변 RAG 평가 (서술형)
# -----------------------------------------------------------------------------
prompt_rag_answer_analysis = (
    SYSTEM_RULES
    + """
{persona_description}
{evaluation_focus}
아래 자료를 종합하여 지원자의 답변을 평가하세요. 점수 대신 '서술형' 의견을 제시합니다.
**[중요] '평가 기준'을 최우선으로 고려하여 답변을 분석하고 피드백을 작성해야 합니다.**

면접 질문: {question}
답변: {answer}

[평가 기준]
{evaluation_criteria}

[자료 1] 내부: {internal_check}
[자료 2] 웹: {web_result}

[요구사항]
- 핵심 주장 1~2개만 검증.
- 근거 표기: "[자료 1 기반]" 또는 "[자료 2 웹 검색 기반]".
- '평가 기준'에 명시된 Rubric과 기대 답변(Expected Points)을 바탕으로 답변의 강점과 약점을 분석하세요.
- 'transition_phrase'에는 방금 들은 답변을 인정하고 다음 질문으로 넘어가는 **매우 자연스럽고 다양한** 연결 구문을 생성하세요. **절대 매번 똑같은 패턴을 사용해서는 안 됩니다.**
  - **규칙 1 (다양성):** 실제 사람이 대화하듯, 때로는 간단하게("네, 알겠습니다."), 때로는 답변 내용을 짧게 언급하며("말씀해주신 [핵심 내용] 경험이 인상적이네요."), 때로는 감탄사로("흥미롭군요.") 시작하는 등, 다양한 표현을 사용해야 합니다.
  - **규칙 2 (간결성):** 대부분의 경우, 한 문장으로 간결하게 표현하세요.
  - **규칙 3 (자기소개):** 자기소개 답변 후에는 "네, 자기소개 잘 들었습니다. 그럼 이제 본격적인 질문을 시작하겠습니다." 와 같이 명확한 전환 문구를 사용하세요.
  - **나쁜 예시 (반복 패턴):** "네, ...에 대한 설명 잘 들었습니다. 그럼 다음 질문으로 넘어가 보겠습니다." 라는 구조를 반복해서 사용하지 마세요.

[출력]
{{
  "claims_checked": [
    {{"claim": "...", "evidence_source": "[자료 1 기반|자료 2 웹 검색 기반]", "verdict": "지원|반박|불충분", "rationale": "..."}}
  ],
  "analysis": "... (300~600자, 평가 기준에 근거하여 작성)",
  "feedback": "... (3~5문장, 평가 기준에 근거하여 개선점을 중심으로 작성)",
  "transition_phrase": "... (1-2 문장의 자연스러운 연결 구문)"
}}
"""
    + prompt_json_output_only
)
