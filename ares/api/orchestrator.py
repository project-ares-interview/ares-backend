# ares/api/orchestrator.py
import json
import re
from typing import Dict, Any, Callable, List, Optional
import logging

from ares.api.services.prompts import (
    # analysis chain
    prompt_identifier, prompt_extractor, prompt_scorer, prompt_score_explainer,
    prompt_coach, prompt_model_answer, prompt_rag_answer_analysis, prompt_bias_checker,
    # classification
    prompt_intent_classifier,
    # followups
    prompt_followup_v2,
)
from ares.api.services.rag.bot.utils import normalize_llm_json

logger = logging.getLogger(__name__)

# ---- LLM 호출기 (주입용 타입 별칭) ----
LLMFn = Callable[[str, float, int], Dict[str, Any]]

def run_prompt(llm: LLMFn, prompt_template: str, **kwargs) -> Dict[str, Any]:
    """
    주어진 프롬프트 템플릿을 포맷팅하고, LLM을 호출한 뒤, 결과를 정규화된 JSON으로 파싱합니다.
    """
    try:
        prompt = prompt_template.format(**kwargs)
        raw_result = llm(prompt, temperature=0.2, max_tokens=2048)
        normalized_result = normalize_llm_json(raw_result)
        if not isinstance(normalized_result, dict):
            logger.warning(f"Prompt did not return a dict: {prompt[:100]}...")
            return {"error": "Invalid JSON format", "raw": str(normalized_result)}
        return normalized_result
    except KeyError as e:
        logger.error(f"Missing key in prompt format: {e}. Prompt: {prompt_template[:200]}...")
        raise e  # Re-raise the error to be caught by the main try-except block
    except Exception as e:
        logger.error(f"Error running prompt: {e}", exc_info=True)
        return {"error": str(e)}

# ---- Sanitizer: 없는 수치/사실 제거 (지원자 자료 근거화) ----
def _extract_numbers(text: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?\s*(?:%|배|개월|주|일|시간)?", text)

def sanitize_against_resume(text: str, resume_blob: str) -> str:
    if not isinstance(text, str): return ""
    original_text = text
    for tok in set(_extract_numbers(text)):
        if tok not in resume_blob:
            text = text.replace(tok, "구체적인 수치")
    
    text = re.sub(r"(구체적인 수치)\s*향상", "얼마나 향상", text)
    text = re.sub(r"(구체적인 수치)\s*감소", "얼마나 감소", text)
    text = re.sub(r"(구체적인 수치)\s*단축", "얼마나 단축", text)

    if original_text != text:
        logger.info("[SANITIZED] before=%s || after=%s", original_text, text)
    return text

# ---- 턴 오케스트레이션 ----
def run_turn_chain(
    llm: LLMFn,
    question: str,
    user_answer: str,
    resume_blob: str,
    jd_text: str,
    company_name: str,
    company_context: str, # <-- 추가
    job_title: str,
    persona: Dict[str, Any],
    ideal_candidate_profile: str,
    transcript_context: str,
    plan_item_meta: Dict[str, Any],
    phase: str,
) -> Dict[str, Any]:
    """
    답변 하나를 처리하는 전체 프롬프트 체인을 실행합니다.
    """
    try:
        # --- 컨텍스트 준비 ---
        full_context = {
            "question": question,
            "user_answer": user_answer,
            "resume_context": resume_blob,
            "jd_context": jd_text,
            "company_name": company_name,
            "company_context": company_context, # <-- 추가
            "job_title": job_title,
            "persona_description": persona.get("persona_description", ""),
            "evaluation_focus": persona.get("evaluation_focus", ""),
            "ideal_candidate_profile": ideal_candidate_profile,
            "transcript_context": transcript_context,
            "retrieved_ncs_details": "(NCS 정보)",
            "role": job_title,
            "phase": phase,
            # plan_item_meta에서 .get()으로 안전하게 값을 추출하고 기본값 설정
            "question_type": plan_item_meta.get("question_type", "star"),
            "objective": plan_item_meta.get("objective", ""),
            "kpi": plan_item_meta.get("kpi", []),
        }

        # 1) 의도 분류
        intent_out = run_prompt(llm, prompt_intent_classifier, question=question, answer=user_answer)
        intent = intent_out.get("intent", "ANSWER")
        if intent != "ANSWER":
            return {"intent": intent, "analysis": {"feedback": "답변 의도가 아니므로 상세 분석을 건너뜁니다."}}

        # 2) 프레임워크 식별
        ident_out = run_prompt(llm, prompt_identifier, **full_context)
        framework = (ident_out.get("frameworks", []) or ["COMPETENCY"])[0].split('+')[0]

        # 3) 요소 추출
        component_map = {
            "STAR": ["situation", "task", "action", "result"],
            "CASE": ["problem", "structure", "analysis", "recommendation"],
            "COMPETENCY": ["competency", "behavior", "impact"],
            "SYSTEMDESIGN": ["requirements", "trade_offs", "architecture", "risks"],
        }
        component_list = component_map.get(framework, component_map["COMPETENCY"])
        extractor_out = run_prompt(llm, prompt_extractor, 
                                   framework_name=framework, 
                                   component_list=json.dumps(component_list, ensure_ascii=False),
                                   analysis_key="extracted",
                                   **full_context)

        # 4) 채점
        scorer_out = run_prompt(llm, prompt_scorer, framework_name=framework, **full_context)
        
        # 5) 점수 해설
        expl_out = run_prompt(llm, prompt_score_explainer, 
                              framework=framework,
                              scores_main=json.dumps(scorer_out.get("scores_main", {}), ensure_ascii=False),
                              scores_ext=json.dumps(scorer_out.get("scores_ext", {}), ensure_ascii=False),
                              scoring_reason=scorer_out.get("scoring_reason", ""),
                              **full_context)

        # 6) 코칭
        coach_out = run_prompt(llm, prompt_coach, scoring_reason=scorer_out.get("scoring_reason", ""), **full_context)

        # 7) 모범답안
        model_out = run_prompt(llm, prompt_model_answer, **full_context)

        # 8) 편향 점검
        texts_to_check = " ".join(filter(None, [
            str(coach_out.get("strengths")), str(coach_out.get("improvements")), coach_out.get("feedback"),
            model_out.get("model_answer")
        ]))
        bias_out = run_prompt(llm, prompt_bias_checker, any_text=texts_to_check)
        if bias_out.get("flagged"):
            logger.warning(f"Potential bias detected in generated text for session.")

        # 9) 꼬리질문 생성
        fu_out = run_prompt(llm, prompt_followup_v2, 
                            **full_context,
                            latest_answer=user_answer,
                            analysis_summary=json.dumps(expl_out, ensure_ascii=False),
                            evaluation_criteria=json.dumps(plan_item_meta.get("rubric", {}), ensure_ascii=False),
                            ncs=full_context["retrieved_ncs_details"],
                           )
        
        clean_followups = [sanitize_against_resume(q, resume_blob) for q in fu_out.get("followups", [])]

        # 10) 최종 결과 조합
        final_analysis = {
            "question_id": plan_item_meta.get("id", "unknown"),
            "question": question,
            "answer": user_answer,
            "analysis": expl_out.get("overall_tip", ""),
            "feedback": coach_out.get("feedback", ""),
            "scoring": scorer_out,
            "coaching": coach_out,
            "model_answer": model_out,
        }

        return {
            "intent": intent,
            "analysis": final_analysis,
            "followups": clean_followups,
            "transition_phrase": fu_out.get("transition_phrase", "네, 다음 질문으로 넘어가겠습니다."),
        }
    except Exception as e:
        logger.error(f"Turn chain failed: {e}", exc_info=True)
        return {"intent": "ERROR", "analysis": {"feedback": f"답변 분석 중 오류가 발생했습니다: {e}"}}