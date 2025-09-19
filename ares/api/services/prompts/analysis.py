# ares/api/services/prompts/analysis.py
"""
Prompts for analyzing candidate's answers.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

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
{
  "frameworks": ["STAR", "CASE+M", "..."],
  "company_values_summary": "..."
}
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
{
  "{analysis_key}": {
    "요소1": "요약1", "요소2": "요약2", "..."
  }
}
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
당신은 위 관점을 가진 채점관입니다. 아래의 [평가 기준]을 고려하여 [{framework_name}] 프레임워크 규칙에 따라 점수를 매기세요.
[평가 기준: 직무 역량 ({role} 직무, NCS 기반)]
{retrieved_ncs_details}
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
{
  "framework": "STAR|SYSTEMDESIGN|CASE|COMPETENCY",
  "scores_main": {"요소명": 0},
  "scores_ext": {"challenge": 0, "learning": 0, "metrics": 0},
  "scoring_reason": "300~600자 요약"
}
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
{
  "framework": "STAR|SYSTEMDESIGN|CASE|COMPETENCY",
  "calibration": [
    {"element": "요소명", "given": 0, "max": 20, "gap": 20,
      "why_not_max": "...", "how_to_improve": ["...", "..."]}
  ],
  "ext_calibration": [
    {"element": "challenge|learning|metrics", "given": 0, "max": 10, "gap": 10,
      "why_not_max": "...", "how_to_improve": ["..."]}
  ],
  "overall_tip": "다음 답변에서 점수를 올리기 위한 우선순위 2~3가지"
}
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
{
  "strengths": ["...인용...' ...설명", "..."],
  "improvements": ["...인용...' ...개선 제안", "..."],
  "feedback": "총평(3~5문장). {company_name}의 다음 단계 진입 조언 포함"
}
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
{
  "flagged": true,
  "issues": [
    {"span": "...", "category": "편향|공격성|차별적 가정|민감정보 오남용|과도한 확신|기타",
      "reason": "...", "suggested_fix": "...", "severity": "low|medium|high"}
  ],
  "sanitized_text": "문제를 수정해 공정/중립적으로 재작성한 전체 텍스트(가능하면 원문과 유사 길이 유지)"
}
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
[참고(NCS)]
{retrieved_ncs_details}
[규칙]
- "model_answer": 400~800자
- 개선점 마커: "[추가]", "[정정]" 총 5개 이하, 동일 문장 중복 마커 금지
- "model_answer_framework": STAR|CASE|SYSTEMDESIGN|COMPETENCY 중 선택
- "selection_reason": 선택 프레임워크의 2~3개 요소 매핑 근거 간결 제시
[출력]
{
  "model_answer": "...",
  "model_answer_framework": "...",
  "selection_reason": "..."
}
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
- 핵심 주장 1~2개만 검증.
- 근거 표기: "[자료 1 기반]" 또는 "[자료 2 웹 검색 기반]".
면접 질문: {question}
답변: {answer}
[자료 1] 내부: {internal_check}
[자료 2] 웹: {web_result}
[출력]
{
  "claims_checked": [
    {"claim": "...", "evidence_source": "[자료 1 기반|자료 2 웹 검색 기반]", "verdict": "지원|반박|불충분", "rationale": "..."}
  ],
  "analysis": "... (300~600자)",
  "feedback": "... (3~5문장)"
}
"""
    + prompt_json_output_only
)
