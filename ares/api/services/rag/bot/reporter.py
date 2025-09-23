# ares/api/services/rag/bot/reporter.py
"""
Final Report generator for the RAG Interview Bot.

- 대화 이력, 구조화 점수, 서술 분석을 종합하여 최종 리포트를 생성합니다.
- 출력은 JSON 형식(요약/강점-약점/향후 개선 가이드/스코어 테이블 등)으로 가정합니다.
"""

import json
from typing import Any, Dict, List, Optional
import numpy as np

from ares.api.utils.ai_utils import safe_extract_json
from ares.api.utils.common_utils import get_logger
from ares.api.services.prompts import (
    prompt_detailed_overview,
    prompt_rag_json_correction,
    prompt_thematic_summary,
)
from .base import RAGBotBase
from .utils import _truncate, _debug_print_raw_json

log = get_logger(__name__)

class ReportGenerator:
    def __init__(self, bot: RAGBotBase):
        self.bot = bot

    def build_report(
        self, 
        transcript: List[Dict[str, Any]], 
        structured_scores: List[Dict[str, Any]],
        interview_plan: Optional[Dict[str, Any]] = None,
        full_resume_analysis: Optional[Dict[str, Any]] = None,
        full_contexts: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        try:
            # 1단계: 데이터 가공, 그룹핑 및 주제별 요약 생성
            themed_feedback, validation_errors = self._process_group_and_summarize(structured_scores)
            if validation_errors:
                log.warning(f"[Report Validation Failed] Session({self.bot.session_id}): {validation_errors}")

            # 2단계: Python 기반으로 구조적 데이터 생성
            all_dossiers = [item for theme in themed_feedback for item in theme.get("details", [])]
            strengths_matrix, weaknesses_matrix = self._build_evidence_matrices(all_dossiers)
            score_aggregation = self._aggregate_scores(all_dossiers)
            hiring_recommendation = self._make_hiring_recommendation(score_aggregation)

            # 3단계: LLM을 통한 서술적 분석 생성
            contexts_for_llm = self._prepare_llm_contexts(
                transcript, interview_plan, full_resume_analysis, all_dossiers, full_contexts
            )
            llm_analysis = self._generate_narrative_analysis(contexts_for_llm)

            # 4단계: 모든 데이터 종합
            final_report = {
                **llm_analysis,
                "strengths_matrix": strengths_matrix,
                "weaknesses_matrix": weaknesses_matrix,
                "score_aggregation": score_aggregation,
                "hiring_recommendation": hiring_recommendation,
                "question_by_question_feedback": themed_feedback, # 새로운 구조 적용
                "original_source_documents": full_contexts,
                "original_interview_plan": interview_plan,
                "full_resume_analysis": full_resume_analysis,
            }
            
            # 5단계: 최종 리포트 검증
            validation_errors = self._validate_final_report(final_report)
            if validation_errors:
                final_report["validation_errors"] = validation_errors
                log.warning(f"[Report Validation Failed] Session({self.bot.session_id}): {validation_errors}")

            return final_report

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"report_build_failed: {e}"}

    def _process_group_and_summarize(self, structured_scores: List[Dict]) -> tuple[List[Dict], List[str]]:
        # 1. 개별 dossier 처리 및 검증
        all_dossiers, errors = self._process_and_validate_dossiers(structured_scores)
        
        # 2. 주 질문 ID 기준으로 그룹핑
        grouped_by_main_q = {}
        for d in all_dossiers:
            main_q_id = d["question_id"].split('-')[0] if d["question_id"] else "unknown"
            if main_q_id not in grouped_by_main_q:
                grouped_by_main_q[main_q_id] = []
            grouped_by_main_q[main_q_id].append(d)

        # 3. 각 그룹에 대해 주제별 요약 생성
        themed_feedback = []
        for main_q_id, group in grouped_by_main_q.items():
            summary = "꼬리질문이 없는 단일 질문입니다."
            if len(group) > 1:
                # LLM 호출하여 요약 생성
                try:
                    topic_block_json = json.dumps(group, ensure_ascii=False)
                    summary_prompt = prompt_thematic_summary.format(topic_block_json=topic_block_json)
                    summary_result = self.bot._chat_json(summary_prompt, temperature=0.2)
                    summary = summary_result.get("thematic_summary", "요약 생성에 실패했습니다.")
                except Exception as e:
                    summary = f"요약 생성 중 오류 발생: {e}"

            themed_feedback.append({
                "main_question_id": main_q_id,
                "thematic_summary": summary,
                "details": group
            })
            
        return themed_feedback, errors



    def _validate_final_report(self, report: Dict) -> List[str]:
        errors = []
        
        # 1. 스키마 키 검증 (레거시 키)
        if "resume_feedback" in report:
            errors.append("Legacy key 'resume_feedback' found. Use 'full_resume_analysis'.")

        # 2. 증거 ID 무결성 검증
        q_feedback = report.get("question_by_question_feedback", [])
        if isinstance(q_feedback, list):
            valid_ids = {q.get("question_id") for q in q_feedback if q.get("question_id")}
        else:
            valid_ids = set()

        def check_evidence(matrix, path):
            if not isinstance(matrix, list): return
            for i, item in enumerate(matrix):
                evidence_list = item.get("evidence", [])
                if not isinstance(evidence_list, list): continue
                for ev in evidence_list:
                    if ev not in valid_ids:
                        errors.append(f"Broken evidence reference in '{path}[{i}]': ID '{ev}' not found.")
        
        check_evidence(report.get("strengths_matrix", []), "strengths_matrix")
        check_evidence(report.get("weaknesses_matrix", []), "weaknesses_matrix")

        # 3. Enum 값 검증
        allowed_intents = {"icebreaking", "self_intro", "motivation", "star", "competency", "case", "system", "hard", "wrapup", "custom", None, "N/A"}
        for q in q_feedback:
            intent = q.get("question_intent")
            if intent not in allowed_intents:
                errors.append(f"Invalid 'question_intent' in question_id '{q.get('question_id')}': '{intent}'")

        return errors


    def _process_and_validate_dossiers(self, structured_scores: List[Dict]) -> tuple[List[Dict], List[str]]:
        per_question_dossiers = []
        errors = []
        valid_ids = {score.get("question_id") for score in structured_scores if score.get("question_id")}

        for score_data in structured_scores:
            qid = score_data.get("question_id", f"unknown_{len(per_question_dossiers)}")
            
            # Enum 통일 (예시)
            q_intent = score_data.get("question_intent")
            allowed_intents = {"icebreaking", "self_intro", "motivation", "star", "competency", "case", "system", "hard", "wrapup", None, "N/A"}
            if q_intent not in allowed_intents:
                q_intent = "custom" # 혹은 에러 처리

            dossier = {
                "question_id": qid,
                "question": score_data.get("question", "N/A"),
                "question_intent": q_intent,
                "evaluation": {
                    "applied_framework": score_data.get("scoring", {}).get("framework", "N/A"),
                    "scores_main": score_data.get("scoring", {}).get("scores_main", {}),
                    "scores_ext": score_data.get("scoring", {}).get("scores_ext", {}),
                    "feedback": score_data.get("feedback", "N/A"),
                    "evidence_quote": score_data.get("answer", "")
                },
                "model_answer": score_data.get("model_answer", {}).get("model_answer", "N/A"),
                "coaching": score_data.get("coaching", {}),
            }
            per_question_dossiers.append(dossier)
        
        # 여기서 evidence 무결성 검증은 _build_evidence_matrices에서 수행
        return per_question_dossiers, errors

    def _build_evidence_matrices(self, dossiers: List[Dict]) -> tuple[List[Dict], List[Dict]]:
        import re

        def _extract_theme(sentence: str) -> str:
            # '인용구' ... 설명 구조에서 설명 부분만 추출
            parts = sentence.split("' ...")
            explanation = parts[-1]

            # 패턴 1: "OOO은(는) XXX의 가치와 부합합니다"
            match = re.search(r"([\w\s]+?)(은|는)\s+SK케미칼의", explanation)
            if match:
                return match.group(1).strip()

            # 패턴 2: "OOO을(를) 보여줍니다"
            match = re.search(r"([\w\s]+?)(을|를)\s+(보여줍니다|보여주었습니다|나타냅니다)", explanation)
            if match:
                return match.group(1).strip()

            # 패턴 3: "OOO이(가) 돋보입니다"
            match = re.search(r"([\w\s]+?)이\/가\s+(돋보입니다|인상적입니다)", explanation)
            if match:
                return match.group(1).strip()
            
            # 패턴 4: "OOO 능력/경험/자세"
            match = re.search(r"([\w\s]+(능력|경험|자세|역량))", explanation)
            if match:
                return match.group(1).strip()

            # 폴백: 첫 5단어
            return " ".join(explanation.strip().split()[:5])

        strengths = {}
        weaknesses = {}
        
        for d in dossiers:
            qid = d.get("question_id")
            if not qid: continue

            coaching = d.get("coaching", {})
            for s_sentence in coaching.get("strengths", []):
                theme = _extract_theme(s_sentence)
                if theme not in strengths: strengths[theme] = []
                strengths[theme].append(qid)
            
            for w_sentence in coaching.get("improvements", []):
                theme = _extract_theme(w_sentence)
                if theme not in weaknesses: weaknesses[theme] = {"evidence": [], "severity": "medium"}
                weaknesses[theme]["evidence"].append(qid)

        return [{"theme": k, "evidence": list(set(v))} for k, v in strengths.items()], \
               [{"theme": k, "severity": v["severity"], "evidence": list(set(v["evidence"]))} for k, v in weaknesses.items()]

    def _aggregate_scores(self, dossiers: List[Dict]) -> Dict:
        # 점수 집계에서 제외할 질문 유형
        SCORED_EXCLUDED = {"icebreaking", "wrapup", "unknown", None, "N/A"}
        
        # 프레임워크별 기본 요소 만점 (확장 가능)
        FRAMEWORK_MAX_SCORES = {
            "STAR": 20 * 4, "COMPETENCY": 20 * 3, "CASE": 20 * 4, 
            "SYSTEMDESIGN": 20 * 4, "HARD": 20 * 4, "SELF_INTRO": 20 * 2, "MOTIVATION": 20*1
        }
        EXT_MAX_SCORE = 10 * 3

        def _normalize_score(score, max_score):
            return (score / max_score * 100) if max_score > 0 else 0

        scores_by_type = {}
        ext_scores = {"challenge": [], "learning": [], "metrics": []}

        for d in dossiers:
            evaluation = d.get("evaluation", {})
            framework = evaluation.get("applied_framework", "unknown").lower()

            if framework in SCORED_EXCLUDED:
                continue

            scores_main = evaluation.get("scores_main", {})
            scores_ext = evaluation.get("scores_ext", {})

            if scores_main and isinstance(scores_main, dict):
                total_main_score = sum(scores_main.values())
                # 프레임워크 이름을 대문자로 변환하여 MAX_SCORES 딕셔너리에서 찾음
                max_main_score = FRAMEWORK_MAX_SCORES.get(framework.upper(), 20 * len(scores_main))
                normalized_main = _normalize_score(total_main_score, max_main_score)
                
                if framework not in scores_by_type: scores_by_type[framework] = []
                scores_by_type[framework].append(normalized_main)

            if scores_ext and isinstance(scores_ext, dict):
                for key in ext_scores.keys():
                    if key in scores_ext:
                        ext_scores[key].append(scores_ext[key] * 10)

        main_avg = {k: np.mean(v) if v else 0 for k, v in scores_by_type.items()}
        ext_avg = {k: np.mean(v) if v else 0 for k, v in ext_scores.items()}

        return {
            "main_avg": {k: round(v) for k, v in main_avg.items()},
            "ext_avg": {k: round(v) for k, v in ext_avg.items()},
            "calibration": "Scores are normalized to a 100-point scale. 'main_avg' is the average score for each question type. 'ext_avg' is the average for challenge, learning, and metrics across all relevant questions."
        }

    def _make_hiring_recommendation(self, score_aggregation: Dict) -> str:
        main_avg_scores = list(score_aggregation.get("main_avg", {}).values())
        ext_avg_scores = list(score_aggregation.get("ext_avg", {}).values())
        
        # main_avg와 ext_avg 딕셔너리가 비어있을 경우를 대비
        mean_main = np.mean(main_avg_scores) if main_avg_scores else 0
        mean_ext = np.mean(ext_avg_scores) if ext_avg_scores else 0

        # 1. 종합 점수 계산
        weighted_score = 0.7 * mean_main + 0.3 * mean_ext

        # 2. 최소 기준(Gate) 통과 여부 확인
        metrics_score = score_aggregation.get("ext_avg", {}).get("metrics", 0)
        star_score = score_aggregation.get("main_avg", {}).get("star", 0)

        gate_metrics_passed = metrics_score >= 20
        gate_star_passed = star_score >= 60
        all_gates_passed = gate_metrics_passed and gate_star_passed

        # 3. 최종 판정
        if weighted_score >= 80 and all_gates_passed:
            return "strong_hire"
        elif weighted_score >= 70 and all_gates_passed:
            return "hire"
        elif weighted_score >= 60:
            return "lean_hire"
        else:
            return "no_hire"

    def _prepare_llm_contexts(self, *args) -> Dict:
        transcript, interview_plan, full_resume_analysis, per_question_dossiers, full_contexts = args
        transcript_digest = json.dumps(transcript, ensure_ascii=False)
        plan_json = json.dumps(interview_plan or {}, ensure_ascii=False)
        resume_json = json.dumps((full_resume_analysis or {}), ensure_ascii=False)
        dossiers_json = json.dumps(per_question_dossiers, ensure_ascii=False)
        contexts_json = json.dumps(full_contexts or {}, ensure_ascii=False)
        
        return {
            "interview_plan_json": _truncate(plan_json, 4000),
            "resume_feedback_json": _truncate(resume_json, 3000),
            "transcript_digest": _truncate(transcript_digest, 8000),
            "per_question_dossiers": _truncate(dossiers_json, 16000),
            "full_contexts_json": _truncate(contexts_json, 8000),
        }

    def _generate_narrative_analysis(self, contexts: Dict) -> Dict:
        persona_desc = self.bot.persona["persona_description"].replace("{company_name}", self.bot.company_name).replace("{job_title}", self.bot.job_title)
        final_report_goal = "CANDIDATE's overall performance, highlighting strengths, weaknesses, and providing a clear hiring recommendation."

        prompt_overview = (
            prompt_detailed_overview
            .replace("{persona_description}", persona_desc)
            .replace("{final_report_goal}", final_report_goal)
            .replace("{evaluation_focus}", self.bot.persona.get("evaluation_focus", ""))
            .replace("{company_name}", self.bot.company_name)
            .replace("{job_title}", self.bot.job_title)
            .replace("{resume_feedback_json}", contexts["resume_feedback_json"])
            .replace("{transcript_digest}", contexts["transcript_digest"])
            .replace("{per_question_dossiers}", contexts["per_question_dossiers"])
            .replace("{full_contexts_json}", contexts["full_contexts_json"])
            .replace("{interview_plan_json}", contexts["interview_plan_json"])
        )

        raw_final_str = self.bot._chat_raw_json_str(prompt_overview, temperature=0.3, max_tokens=4000)
        llm_result = safe_extract_json(raw_final_str)

        if not llm_result:
            # 파싱 실패 시 복구 로직
            corrected_raw = self.bot._chat_json_correction(prompt_overview, raw_final_str)
            llm_result = safe_extract_json(corrected_raw)

        return llm_result or {"error": "Failed to parse narrative analysis"}
