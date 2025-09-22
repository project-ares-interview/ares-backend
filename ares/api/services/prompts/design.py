# ares/api/services/prompts/design.py
"""
Prompts for designing the interview plan and analyzing resumes.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

# -----------------------------------------------------------------------------
# 면접 설계자 (Interview Designer) — 레거시 3단계 요약형
# -----------------------------------------------------------------------------
prompt_interview_designer = (
    SYSTEM_RULES
    + """
{persona_description}
{question_style_guide}
당신은 위의 관점을 가진 면접 설계자입니다. 아래 정보를 바탕으로, 지원자의 역량을 당신의 관점에서 체계적으로 검증할 수 있는 3단계 구조화 면접 계획을 수립하고, 각 단계에 맞는 핵심 질문을 1~2개씩 생성하세요.
각 질문은 200자 이내.
난이도 지침: {difficulty_instruction}
[[최신 사업 요약]]  (1200자 이내)
{business_info}
[[직무 기술서 (JD)]]
{jd_context}
[[지원자 이력서 요약]]
{resume_context}
[[지원자 리서치 정보]]
{research_context}
{ncs_info}
[출력 JSON]
{{
  "plan": [
    {{ "stage": "경험/역량 검증", "objectives": ["..."], "questions": ["...", "..."] }},
    {{ "stage": "상황/케이스 분석", "objectives": ["..."], "questions": ["..."] }},
    {{ "stage": "조직 적합성 및 성장 가능성", "objectives": ["..."], "questions": ["..."] }}
  ]
}}
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# V2: 인터뷰 설계자 (Full Simulation Plan with phases/types/mix/curve)
# -----------------------------------------------------------------------------
prompt_interview_designer_v2 = (
    SYSTEM_RULES
    + """
{persona_description}
{question_style_guide}
당신은 위 관점을 가진 '면접 설계자'입니다. 아래 정보를 바탕으로 20~30분 완전한 시뮬레이션용 계획을 설계하세요.
요구사항:
- phases: intro → core → wrapup 순서
- 각 item은 question_type을 다음 중 하나로: ["icebreaking","self_intro","motivation","star","competency","case","system","hard","wrapup"]
- **[매우 중요] 각 질문은 하나의 명확한 목적에만 집중해야 합니다. 예를 들어, 경험을 묻는 질문과 지원 동기를 묻는 질문을 절대 한 문장으로 합치지 마세요.**
- **[매우 중요] 질문 생성 시 'STAR 방식으로', 'CASE 기법으로' 등 평가 프레임워크의 이름을 절대 직접 언급하지 마세요. 대신, 해당 프레임워크로 답변할 수밖에 없는 자연스러운 행동/경험 기반 질문을 하세요.**
- **[중요] `회사의 인재상`을 참고하여, 지원자의 가치관과 경험이 회사의 문화와 부합하는지 확인할 수 있는 질문을 1~2개 포함하세요.**
- icebreaking 질문은 지원자가 면접 장소에 도착하기까지의 과정이나 현재 컨디션 등, 면접 당일의 상황과 관련된 아주 가벼운 스몰 토크여야 합니다. (좋은 예: '오늘 오시는 길은 어떠셨나요?', '점심은 드셨나요?' / 나쁜 예: '가장 감명깊게 읽은 책은?')
- difficulty_curve: ["easy","normal","hard"] (core 내 질문이 자연스럽게 상승)
- mix_ratio: {{"star":0.x,"case":0.x,"competency":0.x,"system":0.x}} 합 1.0 (core 기준)
- 각 question은 최대 1문장(≤200자), followups는 1~3개
- KPI/NCS 맥락이 있으면 items[*].kpi 필드에 ["OEE","MTBF"] 등 포함 가능
- **[중요] 모든 질문(items)에는 'expected_points'와 'rubric'을 반드시 포함해야 합니다.**
- **expected_points**: 해당 질문을 통해 확인하고자 하는 핵심 역량 키워드 또는 기대 답변 포인트를 3~5개 나열합니다.
- **rubric**: "매우우수/우수/보통/약간미흡/미흡" 5단계의 평가 기준을 구체적인 서술형으로 정의하고, 각 등급에 50/40/30/20/10점의 점수를 부여합니다.

A good interview plan contains a balanced mix of questions:
- **Resume-Specific Questions:** At least half of the questions in the 'main' phase should be specific, probing into details, projects, or quantified achievements mentioned in the candidate's resume. This is crucial to verify their experience. For example, if the resume mentions 'led a project that improved efficiency by 20%', ask 'Can you walk me through the project where you improved efficiency by 20%? What was your specific role?'.
- **Competency Questions:** The other questions can be broader behavioral or situational questions to assess core competencies relevant to the job description that are not explicitly covered in the resume.

[[최신 사업 요약]]  
{business_info}

[[회사의 인재상]]
{ideal_candidate_profile}

[[JD]]  
{jd_context}
[[이력서]]  
{resume_context}
[[리서치]]  
{research_context}
[[NCS 요약/키워드]]  
{ncs_info}
난이도 지침: {difficulty_instruction}

[출력 JSON 스키마]
{{
  "language": "ko",
  "difficulty_curve": ["easy","normal","hard"],
  "mix_ratio": {{"star":0.4,"case":0.3,"competency":0.2,"system":0.1}},
  "phases": [
    {{
      "phase": "intro",
      "items": [
        {{
          "question_type": "icebreaking",
          "question": "...",
          "followups": ["..."],
          "expected_points": ["긴장 완화", "분위기 조성"],
          "rubric": [
            {{"label": "매우우수", "score": 50, "desc": "편안하고 자연스럽게 대답하며 긍정적인 분위기를 조성함."}},
            {{"label": "보통", "score": 30, "desc": "간단하게 대답하며 무난한 수준의 상호작용을 보임."}},
            {{"label": "미흡", "score": 10, "desc": "단답형으로 대답하거나 긴장한 기색이 역력함."}}
          ]
        }},
        {{"question_type":"self_intro","question":"...", "followups":["..."], "expected_points": ["..."], "rubric": [...]}},
        {{"question_type":"motivation","question":"...", "followups":["..."], "expected_points": ["..."], "rubric": [...]}}
      ]
    }},
    {{
      "phase": "core",
      "items": [
        {{
          "question_type": "star",
          "question": "...",
          "followups": ["..."],
          "kpi": ["OEE","MTBF"],
          "expected_points": ["문제 정의(Situation/Task)", "본인의 역할/행동(Action)", "구체적인 결과(Result)", "정량적 성과", "배운 점"],
          "rubric": [
            {{"label": "매우우수", "score": 50, "desc": "STAR 구조에 맞춰 모든 요소를 구체적이고 논리적으로 설명하며, 정량적 성과를 명확히 제시함."}},
            {{"label": "우수", "score": 40, "desc": "STAR 구조에 맞춰 대부분의 요소를 설명하지만, 일부 내용의 구체성이 다소 부족함."}},
            {{"label": "보통", "score": 30, "desc": "STAR 구조를 따르려 노력했으나, 일부 요소가 누락되거나 설명이 불분명함."}},
            {{"label": "약간미흡", "score": 20, "desc": "자신의 행동이나 결과에 대한 설명이 부족하고, 대부분 상황 설명에 치중함."}},
            {{"label": "미흡", "score": 10, "desc": "질문의 의도를 파악하지 못하고, 경험을 제대로 설명하지 못함."}}
          ]
        }},
        {{"question_type":"competency","question":"...","followups":["..."], "expected_points": ["..."], "rubric": [...]}},
        {{"question_type":"case","question":"...","followups":["..."], "kpi":["..."], "expected_points": ["..."], "rubric": [...]}},
        {{"question_type":"system","question":"...","followups":["..."], "expected_points": ["..."], "rubric": [...]}},
        {{"question_type":"hard","question":"...","followups":["..."], "expected_points": ["..."], "rubric": [...]}}
      ]
    }},
    {{
      "phase": "wrapup",
      "items": [
        {{"question_type":"wrapup","question":"마지막으로 질문이나 하고 싶은 말이 있으신가요?", "followups":[], "expected_points": ["회사/직무에 대한 관심도", "입사 의지", "마지막 어필"], "rubric": [...]}}
      ]
    }}
  ]
}}
출력 전 자가검증 체크리스트:
- 질문 중복/의도 충돌 없음
- core에서 난이도 easy→normal→hard 흐름 유지
- mix_ratio 준수(±1개 허용)
- 직무 적합성(KPI/NCS) 커버됨
- **모든 질문에 expected_points와 rubric이 포함되었는가**
"""
    + prompt_json_output_only
)

# -----------------------------------------------------------------------------
# 이력서 RAG 비교 분석 (Resume Analyzer)
# -----------------------------------------------------------------------------
prompt_resume_analyzer = (
    SYSTEM_RULES
    + """
{persona_description}
당신은 위의 관점을 가진 시니어 리크루터입니다. 아래의 [회사 사업 요약]과 [지원자 이력서]를 비교 분석하여, {job_title} 직무 관점의 적합도를 평가하세요.
각 항목 400자 이내.
[회사 사업 요약 (RAG)]
{business_info}
[지원자 이력서]
{resume_context}
[출력 JSON]
{{
  "job_fit_assessment": "...",
  "strengths_and_opportunities": "...",
  "gaps_and_improvements": "..."
}}
"""
    + prompt_json_output_only
)
