# ares/api/services/rag/final_interview_rag.py
from __future__ import annotations

"""
RAG Interview Bot (Facade)

- Base(공통 초기화/저수준 API), Planner(계획), Analyzer(평가/꼬리질문), Reporter(최종 리포트)를
  하나의 파사드 클래스로 묶어 Django View에서 사용하기 쉽게 합니다.

주의:
- Planner는 {"interview_plan":[...]} 형태를 반환합니다.
- Facade는 언제나 "표준 스키마" 상태(self.plan)에 보관하기 위해 normalize를 적용합니다.
"""

import json
import random
from typing import Any, Dict, List, Optional

from ares.api.services.prompts import (
    prompt_identifier,
    prompt_extractor,
    prompt_scorer,
    prompt_coach,
    prompt_model_answer,
    prompt_intent_classifier,
    prompt_rag_answer_analysis,
)
from ares.api.services.company_data import get_company_description
from .bot.base import RAGBotBase
from .bot.planner import InterviewPlanner
from .bot.analyzer import AnswerAnalyzer
from .bot.reporter import ReportGenerator
from .bot.base import RAGBotBase
from .bot.planner import InterviewPlanner
from .bot.analyzer import AnswerAnalyzer
from .bot.reporter import ReportGenerator
from .bot.utils import (
    normalize_interview_plan,
    extract_first_main_question,
    _truncate,
)

class RAGInterviewBot:
    def __init__(
        self,
        company_name: str,
        job_title: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",
        ncs_context: Optional[dict] = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        **kwargs,
    ):
        self.base = RAGBotBase(
            company_name=company_name,
            job_title=job_title,
            difficulty=difficulty,
            interviewer_mode=interviewer_mode,
            ncs_context=ncs_context,
            jd_context=jd_context,
            resume_context=resume_context,
            research_context=research_context,
            **kwargs,
        )
        self.planner = InterviewPlanner(self.base)
        self.analyzer = AnswerAnalyzer(self.base)
        self.reporter = ReportGenerator(self.base)

        # 표준 스키마로 보관되는 현재 계획
        # {"icebreakers":[...], "stages":[{title, questions:[{id,text,followups:[]}, ...]}]}
        self.plan: Dict[str, Any] = {"icebreakers": [], "stages": []}
        self.transcript: List[Dict[str, Any]] = []

    # -----------------------------
    # Plan (설계)
    # -----------------------------
    def design_interview_plan(self) -> Dict[str, Any]:
        """
        Planner의 결과({"interview_plan":[...]})를 받아 원본과 정규화된 버전을 모두 포함하여 반환
        """
        raw_plan = self.planner.design_interview_plan()  # e.g., {"interview_plan": [...], "icebreakers": [...]}
        
        # normalize_interview_plan 함수가 아이스브레이커 분리/처리를 모두 담당
        normalized_plan = normalize_interview_plan(raw_plan or {})

        # self.plan에는 정규화된 계획을 저장하여 기존 로직 호환성 유지
        self.plan = normalized_plan

        # View에서 두 가지 버전을 모두 사용할 수 있도록 dict 형태로 반환
        return {
            "raw_v2_plan": raw_plan,
            "normalized_plan": normalized_plan
        }

    def _get_opening_statement(self) -> str:
        """면접관 모드와 템플릿 조합에 따라 동적인 첫 인사말을 반환합니다."""
        
        # --- 1. 기본 인사 템플릿 ---
        greeting_templates = [
            f"안녕하세요, {self.base.company_name} {self.base.job_title} 직무 면접에 오신 것을 환영합니다.",
            f"반갑습니다. {self.base.company_name} {self.base.job_title} 직무 면접에 참여해주셔서 감사합니다.",
            f"{self.base.company_name} {self.base.job_title} 직무 면접을 시작하겠습니다. 귀한 시간 내주셔서 감사합니다.",
        ]
        
        # --- 2. 면접관 소개 템플릿 (모드별) ---
        mode_templates = {
            "team_lead": [
                "저는 해당 직무의 팀장입니다.",
                "오늘 실무 역량에 대해 함께 이야기를 나눌 팀장입니다.",
                "저는 지원하신 팀의 리더로서, 오늘 면접을 진행하게 되었습니다.",
            ],
            "executive": [
                "저는 임원 면접을 담당하고 있습니다.",
                "오늘 최종 면접을 진행할 임원입니다.",
                "우리 조직과의 적합성을 확인하기 위해 오늘 면접에 참여한 임원입니다.",
            ],
            "default": [
                "오늘 면접을 진행할 면접관입니다.",
            ]
        }
        
        # --- 3. 환영 및 분위기 조성 템플릿 ---
        welcome_templates = [
            "오늘 면접은 편안한 분위기에서 진행될 예정이니, 긴장 푸시고 본인의 경험을 솔직하게 말씀해주시면 됩니다.",
            "이 자리는 평가의 시간이라기보다, 서로에 대해 알아가는 과정이라 생각해주시면 좋겠습니다. 편안하게 임해주세요.",
            "지원자님께서 가진 역량과 경험을 충분히 들을 수 있도록 경청하겠습니다. 솔직하고 편안하게 답변해주시면 감사하겠습니다.",
            "답변이 조금 길어져도 괜찮으니, 본인의 생각을 충분히 말씀해주시기 바랍니다.",
        ]

        # --- 4. 템플릿 무작위 조합 ---
        base_greeting = random.choice(greeting_templates)
        
        introduction_pool = mode_templates.get(self.base.interviewer_mode, mode_templates["default"])
        mode_specific_line = random.choice(introduction_pool)
        
        warm_welcome = random.choice(welcome_templates)
        
        # 최종 인사말 조합
        return f"{base_greeting} {mode_specific_line} {warm_welcome}"

    def get_first_question(self) -> Dict[str, Any]:
        """
        인사말과 함께 동적으로 생성된 아이스브레이킹 질문을 반환합니다.
        실패 시 안전한 폴백 메커니즘을 사용합니다.
        """
        opening_statement = self._get_opening_statement()
        icebreaker_text = ""

        try:
            # 템플릿 기반의 가벼운 질문을 우선적으로 사용
            icebreaker_text = random.choice(ICEBREAK_TEMPLATES_KO)
        except Exception:
            # 템플릿 사용 실패 시 LLM 호출로 폴백
            try:
                icebreaker_text = make_icebreak_question_llm_or_template(self.base._chat_json)
            except Exception:
                # LLM 호출도 실패하면 최종 폴백
                icebreaker_text = "오늘 면접 보러 오시는 길은 어떠셨나요?"

        if icebreaker_text:
            full_question = f"{opening_statement} {icebreaker_text}"
            return {"id": "icebreaker-template-1", "question": full_question}

        # 아이스브레이커 생성에 완전히 실패한 경우, 첫 번째 메인 질문으로 폴백
        qtext, qid = extract_first_main_question(self.plan or {})
        if not qtext:
            return {}  # 계획이 비어있는 극단적인 경우
        
        full_question = f"{opening_statement} {qtext}"
        return {"id": qid or "main-1-1", "question": full_question}

    # -----------------------------
    # Intent Classification
    # -----------------------------
    def classify_user_intent(self, question: str, answer: str) -> str:
        """Classifies the user's intent."""
        prompt = prompt_intent_classifier.format(question=question, answer=answer)
        result = self.base._chat_json(prompt, temperature=0.0)
        return result.get("intent", "ANSWER")

    # -----------------------------
    # Analyze (분석/평가/꼬리질문)
    # -----------------------------
    def analyze_answer_with_rag(self, question: str, answer: str, stage: str, question_item: Optional[Dict] = None) -> dict:
        """
        Analyzes the candidate's answer using a multi-step RAG pipeline.
        """
        print(f"[INFO] 답변 분석 파이프라인 시작: 질문: {question[:50]}...\n답변: {answer[:50]}...")
        if not self.base.rag_ready:
            print("[WARNING] analyze_answer: RAG system is not ready.")
            return {"error": "RAG system is not ready."}

        # --- 컨텍스트 준비 ---
        persona_desc = self.base.persona.get("persona_description", "")
        eval_focus = self.base.persona.get("evaluation_focus", "")
        ncs_details = json.dumps(self.base.ncs_context, ensure_ascii=False)
        
        evaluation_criteria = ""
        if question_item:
            rubric = question_item.get("rubric")
            expected = question_item.get("expected_points")
            criteria_text = "\n[평가 기준]\n"
            if rubric:
                criteria_text += f"- Rubric: {json.dumps(rubric, ensure_ascii=False)}\n"
            if expected:
                criteria_text += f"- Expected Points: {json.dumps(expected, ensure_ascii=False)}\n"
            evaluation_criteria = criteria_text

        # --- 파이프라인 1: 기본 분석 (피드백, 사실 확인) ---
        print("  [1/4] 기본 분석 수행...")
        analysis_prompt = prompt_rag_answer_analysis.format(
            persona_description=persona_desc,
            evaluation_focus=eval_focus,
            question=question,
            answer=answer,
            evaluation_criteria=evaluation_criteria,
            internal_check="(내부 자료 검증 정보 없음)",
            web_result="(웹 검색 결과 없음)"
        )
        base_analysis = self.base._chat_json(prompt=analysis_prompt, temperature=0.2)

        # --- 파이프라인 2: 점수 채점 ---
        print("  [2/4] 점수 채점 수행...")
        framework = question_item.get("question_type", "COMPETENCY").upper() if question_item else "COMPETENCY"
        scorer_prompt = prompt_scorer.format(
            persona_description=persona_desc,
            evaluation_focus=eval_focus,
            framework_name=framework,
            role=self.base.job_title,
            retrieved_ncs_details=ncs_details,
            # Hallucination 방지를 위해 원본 답변 전달
            user_answer=answer 
        )
        scoring_result = self.base._chat_json(prompt=scorer_prompt, temperature=0.1)

        # --- 파이프라인 3: 코칭 (강점/개선점) ---
        print("  [3/4] 코칭 생성 수행...")
        coach_prompt = prompt_coach.format(
            persona_description=persona_desc,
            scoring_reason=scoring_result.get("scoring_reason", ""),
            user_answer=answer,
            resume_context=self.base.resume_context,
            ideal_candidate_profile=get_company_description(self.base.company_name),
            retrieved_ncs_details=ncs_details,
            company_name=self.base.company_name
        )
        coaching_result = self.base._chat_json(prompt=coach_prompt, temperature=0.3)

        # --- 파이프라인 4: 모범 답안 생성 ---
        print("  [4/4] 모범 답안 생성 수행...")
        model_answer_prompt = prompt_model_answer.format(
            persona_description=persona_desc,
            retrieved_ncs_details=ncs_details,
            user_answer=answer,
            resume_context=self.base.resume_context
        )
        model_answer_result = self.base._chat_json(prompt=model_answer_prompt, temperature=0.3)

        # --- 최종 결과 취합 ---
        final_result = {
            "question_id": question_item.get("id") if question_item else "unknown",
            "question": question,
            "question_intent": question_item.get("objective") if question_item else "N/A",
            "answer": answer,
            **base_analysis,
            "scoring": scoring_result,
            "coaching": coaching_result,
            "model_answer": model_answer_result,
        }
        
        # 대화록에 현재 턴 기록 추가
        self.transcript.append({
            "turn": len(self.transcript) + 1,
            "question": question,
            "answer": answer,
            "analysis_summary": {
                "feedback": base_analysis.get("feedback"),
                "scoring_reason": scoring_result.get("scoring_reason")
            }
        })
        
        print(f"[INFO] 답변 분석 파이프라인 완료.")
        return final_result

    def generate_follow_up_question(
        self,
        original_question: str,
        answer: str,
        analysis: Dict,
        stage: str,
        objective: str,
        question_item: Optional[Dict] = None,
        *,
        limit: int = 3,
        **kwargs,
    ) -> List[str]:
        """
        꼬리질문 생성(FSM/뷰에서 호출). 파라미터명은 limit 사용.
        """
        return self.analyzer.generate_follow_up_question(
            original_question=original_question,
            answer=answer,
            analysis=analysis,
            transcript=self.transcript,
            stage=stage,
            objective=objective,
            question_item=question_item,
            limit=limit,
            **kwargs,
        )

    # -----------------------------
    # Report (최종 리포트)
    # -----------------------------
    def build_final_report(
        self, 
        transcript: List[Dict[str, Any]], 
        structured_scores: List[Dict[str, Any]],
        interview_plan: Optional[Dict[str, Any]] = None,
        full_resume_analysis: Optional[Dict[str, Any]] = None,
        full_contexts: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return self.reporter.build_report(
            transcript=transcript, 
            structured_scores=structured_scores,
            interview_plan=interview_plan,
            full_resume_analysis=full_resume_analysis,
            full_contexts=full_contexts
        )

    # -----------------------------
    # (선택) CLI 시연용 워크플로우
    # -----------------------------
    def conduct_interview(self):
        """
        로컬 CLI 테스트용 간단 워크플로우.
        Django에서는 사용하지 않지만, 디버깅 목적으로 유지.
        """
        print("\n🤖 RAG Interview Bot Facade Initializing (Interviewer: {})...".format(self.base.interviewer_mode))

        plan_std = self.design_interview_plan()  # 표준 스키마 {icebreakers, stages}
        if not plan_std or not plan_std.get("stages"):
            print("\n❌ Could not create an interview plan.")
            return

        first = self.get_first_question()
        if not first:
            print("\n⚠️ 첫 질문을 찾지 못하여 폴백 문구를 사용합니다.")
            first = {"id": "FALLBACK-1", "question": "가벼운 아이스브레이킹으로 시작해볼게요. 최근에 재미있게 본 콘텐츠가 있나요?"}

        print(f"\n[Q] {first['question']}")
        user_answer = input("[A] ")  # CLI에서만 사용
        analysis = self.analyze_answer(first["question"], user_answer, role=self.base.job_title)
        print("\n[ANALYSIS]\n", json.dumps(analysis, ensure_ascii=False, indent=2))
