# ares/api/services/prompts/report.py
"""
Prompts for generating final interview reports.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

# -----------------------------------------------------------------------------
# 최종 종합 리포트 (레거시)
# -----------------------------------------------------------------------------
prompt_rag_final_report = (
    SYSTEM_RULES
    + """
{persona_description}
{final_report_goal}
당신은 위의 관점을 가진 채용 책임자입니다. 아래 자료를 종합하여 '최종 역량 분석 종합 리포트'를 작성하세요.
길이 규격:
- assessment_of_plan_achievement: 3~5문장
- overall_summary: 4~7문장
- core_competency_analysis: 정확히 3개(각 evidence 포함)
- question_by_question_feedback: 최대 8개
[자료 1] 면접 전체 요약:
{conversation_summary}
[자료 2] 최초 수립된 면접 계획:
{interview_plan}
[자료 3] RAG 기반 이력서 분석 결과:
{resume_feedback_analysis}
[출력 JSON]
{
  "assessment_of_plan_achievement": "...",
  "overall_summary": "...",
  "core_competency_analysis": [
    { "competency": "핵심 역량 1", "assessment": "[최상]|[상]|[중]|[하]", "evidence": "판단 근거..." },
    { "competency": "핵심 역량 2", "assessment": "[최상]|[상]|[중]|[하]", "evidence": "판단 근거..." },
    { "competency": "핵심 역량 3", "assessment": "[최상]|[상]|[중]|[하]", "evidence": "판단 근거..." }
  ],
  "growth_potential": "...",
  "resume_feedback": {
    "job_fit_assessment": "...",
    "strengths_and_opportunities": "...",
    "gaps_and_improvements": "..."
  },
  "question_by_question_feedback": [
    {
      "question": "면접 질문 1",
      "question_intent": "...",
      "answer": "지원자 답변 1",
      "keyword_analysis": { "job_related_keywords": ["..."], "comment": "..." },
      "evaluation": { "applied_framework": "STAR|CASE|SYSTEMDESIGN|COMPETENCY(+C/L/M 선택)", "feedback": "..." },
      "model_answer": "모범 답변 예시..."
    }
  ]
}
"""
    + prompt_json_output_only
)

# =============================================================================
# 신규: 상세 리포트 (문항 도시에) - 배치 패스
# =============================================================================
prompt_detailed_section = (
    SYSTEM_RULES
    + """
You are a rigorous interview auditor. Return ONLY valid JSON.

[Goal]
For each Q/A below, produce a detailed dossier including:
- question_intent (role/company context)
- model_answer (400~800자, 프레임워크 표기)
- user_answer_structure (framework + present/missing)
- scoring (scores_main/ext, rationale)
- coaching (strengths, improvements, next_steps)
- additional_followups (3개)
- fact_checks (claim-by-claim)
- ncs_alignment (titles mapping)
- risk_notes (hiring risk signals)

[Context]
- company: {company_name}
- role: {job_title}
- persona: {persona_description}
- evaluation_focus: {evaluation_focus}
- business_info: {business_info}
- ncs_titles: {ncs_titles}

[InputItems]
{items}

[Output JSON Schema]
{
  "per_question_dossiers": [
    {
      "question_id": "1-1",
      "question": "...",
      "question_intent": "...",
      "model_answer": "...",
      "user_answer_structure": {
        "framework": "STAR|CASE|SYSTEMDESIGN|COMPETENCY|OTHER",
        "elements_present": ["..."],
        "elements_missing": ["..."]
      },
      "scoring": {
        "applied_framework": "STAR",
        "scores_main": {"clarity": 0, "depth": 0, "evidence": 0, "relevance": 0},
        "scores_ext": {"leadership": 0, "communication": 0, "metrics": 0},
        "scoring_reason": "..."
      },
      "coaching": {
        "strengths": ["..."],
        "improvements": ["..."],
        "next_steps": ["..."]
      },
      "additional_followups": ["Q1","Q2","Q3"],
      "fact_checks": [{"claim":"...","verdict":"지원|불충분|반박","rationale":"..."}],
      "ncs_alignment": ["..."],
      "risk_notes": ["..."]
    }
  ]
}
"""
    + prompt_json_output_only
)

# =============================================================================
# 신규: 상세 리포트 (오버뷰) - 종합 패스
# =============================================================================
prompt_detailed_overview = (
    SYSTEM_RULES
    + """
You are a head interviewer producing a FINAL exhaustive interview report. Return ONLY valid JSON.

[Goal]
Merge per-question dossiers, the interview plan, resume feedback, and transcript to produce:
- overall_summary (2~4 paragraphs)
- interview_flow_rationale: 단계/순서의 의도 및 검증 포인트
- strengths_matrix: 테마 클러스터 + evidence(question_ids)
- weaknesses_matrix: 테마/심각도 + evidence
- score_aggregation: 평균/분산, calibration notes
- missed_opportunities: 기대되던 강답변이 누락된 영역
- potential_followups_global: 5~10개
- resume_feedback: (요약 or 그대로)
- hiring_recommendation: "strong_hire|hire|no_hire" (+ 이유)
- next_actions: 구체적 후속조치
- question_by_question_feedback: 문항 카드(의도/모범답변/추가 follow-up 포함)

[Context]
- company: {company_name}
- role: {job_title}
- persona: {persona_description}
- final_report_goal: {final_report_goal}
- evaluation_focus: {evaluation_focus}

[Inputs]
- interview_plan: {interview_plan_json}
- resume_feedback_analysis: {resume_feedback_json}
- transcript_digest: {transcript_digest}
- per_question_dossiers: {per_question_dossiers}

[Output JSON Schema]
{
  "overall_summary": "...",
  "interview_flow_rationale": "...",
  "strengths_matrix": [{"theme":"...","evidence":["1-2","2-1"]}],
  "weaknesses_matrix": [{"theme":"...","severity":"low|medium|high","evidence":["..."]}],
  "score_aggregation": {
    "main_avg": {},
    "ext_avg": {},
    "calibration": "..."
  },
  "missed_opportunities": ["..."],
  "potential_followups_global": ["..."],
  "resume_feedback": {
    "job_fit_assessment": "...",
    "strengths_and_opportunities": "...",
    "gaps_and_improvements": "..."
  },
  "hiring_recommendation": "strong_hire|hire|no_hire",
  "next_actions": ["..."],
  "question_by_question_feedback": [
    {
      "question_id": "1-1",
      "stage": "...",
      "objective": "...",
      "question": "...",
      "question_intent": "...",
      "evaluation": {
        "applied_framework": "STAR",
        "scores_main": {},
        "scores_ext": {},
        "feedback": "..."
      },
      "model_answer": "...",
      "additional_followups": ["..."]
    }
  ]
}
"""
    + prompt_json_output_only
)
