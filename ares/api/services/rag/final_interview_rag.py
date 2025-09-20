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
from typing import Any, Dict, List, Optional

from ares.api.services.prompts import prompt_rag_answer_analysis
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
        """면접관 모드에 따라 첫 인사말을 반환합니다."""
        base_greeting = f"안녕하세요, {self.base.company_name} {self.base.job_title} 직무 면접에 오신 것을 환영합니다."
        
        # 모드별 추가 설명
        mode_specific_line = ""
        if self.base.interviewer_mode == "team_lead":
            mode_specific_line = "저는 해당 직무의 팀장입니다."
        elif self.base.interviewer_mode == "executive":
            mode_specific_line = "저는 임원 면접을 담당하고 있습니다."

        # 공통 환영 문구
        warm_welcome = "오늘 면접은 편안한 분위기에서 진행될 예정이니, 긴장 푸시고 본인의 경험을 솔직하게 말씀해주시면 됩니다."
        
        # 최종 인사말 조합
        if mode_specific_line:
            return f"{base_greeting} {mode_specific_line} {warm_welcome}"
        else:
            return f"{base_greeting} {warm_welcome}"

    def get_first_question(self) -> Dict[str, Any]:
        """
        표준 스키마(self.plan)에서 첫 질문(인사말 + 아이스브레이킹)을 찾아 반환.
        실패 시 빈 dict.
        """
        opening_statement = self._get_opening_statement()
        
        # 1. 아이스브레이킹 질문이 있는지 확인
        if self.plan and self.plan.get("icebreakers"):
            first_icebreaker = self.plan["icebreakers"][0]
            # 아이스브레이커의 구조에 따라 id와 text를 추출 (구조를 가정)
            qid = first_icebreaker.get("id", "icebreaker-1")
            qtext = first_icebreaker.get("text") or first_icebreaker.get("question")
            if qtext:
                # 인사말과 아이스브레이킹 질문을 결합
                full_question = f"{opening_statement} {qtext}"
                return {"id": qid, "question": full_question}

        # 2. 아이스브레이킹이 없으면, 인사말 + 첫 메인 질문을 반환
        qtext, qid = extract_first_main_question(self.plan or {})
        if not qtext:
            return {}
        
        full_question = f"{opening_statement} {qtext}"
        return {"id": qid, "question": full_question}

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
