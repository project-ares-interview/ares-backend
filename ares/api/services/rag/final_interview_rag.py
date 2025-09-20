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
    prompt_rag_answer_analysis,
    make_icebreak_question_llm_or_template,
    ICEBREAK_TEMPLATES_KO,
)
from .bot.base import RAGBotBase
from .bot.planner import InterviewPlanner
from .bot.analyzer import AnswerAnalyzer
from .bot.reporter import ReportGenerator
from .bot.utils import (
    normalize_interview_plan,
    extract_first_main_question,
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
            # LLM 호출을 self.base._chat_json으로 전달하여 동적 질문 생성
            icebreaker_text = make_icebreak_question_llm_or_template(self.base._chat_json)
        except Exception:
            # LLM 호출 실패 시, 안전하게 하드코딩된 템플릿에서 무작위 선택
            icebreaker_text = random.choice(ICEBREAK_TEMPLATES_KO)

        if icebreaker_text:
            full_question = f"{opening_statement} {icebreaker_text}"
            return {"id": "icebreaker-dynamic-1", "question": full_question}

        # 아이스브레이커 생성에 완전히 실패한 경우, 첫 번째 메인 질문으로 폴백
        qtext, qid = extract_first_main_question(self.plan or {})
        if not qtext:
            return {}  # 계획이 비어있는 극단적인 경우
        
        full_question = f"{opening_statement} {qtext}"
        return {"id": qid or "main-1-1", "question": full_question}

    # -----------------------------
    # Analyze (분석/평가/꼬리질문)
    # -----------------------------
    def analyze_answer_with_rag(self, question: str, answer: str, stage: str) -> dict:
        """
        Analyzes the candidate's answer using RAG.
        """
        print(f"[INFO] 답변 분석 시작: 질문: {question}\n답변: {answer}")
        if not self.base.rag_ready:
            print("[WARNING] analyze_answer: RAG system is not ready.")
            return {"error": "RAG system is not ready."}

        # 프롬프트 플레이스홀더에 실제 값 주입
        # TODO: internal_check, web_result는 현재 구현에서 비어있으므로, 향후 RAG 기능 확장 시 채워야 함
        formatted_prompt = prompt_rag_answer_analysis.format(
            persona_description=self.base.persona.get("persona_description", ""),
            evaluation_focus=self.base.persona.get("evaluation_focus", ""),
            question=question,
            answer=answer,
            internal_check="(내부 자료 검증 정보 없음)",
            web_result="(웹 검색 결과 없음)"
        )

        print(f"[INFO] RAG 기반 답변 분석 프롬프트 생성...")
        
        response_json = self.base._chat_json(
            prompt=formatted_prompt,
            temperature=0.2,
        )
        
        print(f"[INFO] LLM 응답 수신: 분석 결과: {response_json}")
        return response_json

    def generate_follow_up_question(
        self,
        original_question: str,
        answer: str,
        analysis: Dict,
        stage: str,
        objective: str,
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
            stage=stage,
            objective=objective,
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
        resume_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return self.reporter.build_report(
            transcript=transcript, 
            structured_scores=structured_scores,
            interview_plan=interview_plan,
            resume_feedback=resume_feedback
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
