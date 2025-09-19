"""
RAG Interview Bot (Facade)

This file acts as a facade, orchestrating the different components of the RAG bot.
It delegates tasks to specialized modules for planning, analysis, and reporting.
"""

from __future__ import annotations
import traceback
from typing import Any, Dict, List, Optional

from unidecode import unidecode

from .bot.planner import InterviewPlanner
from .bot.analyzer import AnswerAnalyzer
from .bot.reporter import ReportGenerator

class RAGInterviewBot:
    """Facade for the RAG + LLM based structural interview bot."""

    def __init__(
        self,
        company_name: str,
        job_title: str,
        container_name: str,
        index_name: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",
        ncs_context: Optional[dict] = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        *,
        sync_on_init: bool = False,
        **kwargs,
    ):
        print(f"🤖 RAG Interview Bot Facade Initializing (Interviewer: {interviewer_mode})...")
        
        # Common arguments for all sub-modules
        bot_args = {
            "company_name": company_name,
            "job_title": job_title,
            "container_name": container_name,
            "index_name": index_name,
            "difficulty": difficulty,
            "interviewer_mode": interviewer_mode,
            "ncs_context": ncs_context,
            "jd_context": jd_context,
            "resume_context": resume_context,
            "research_context": research_context,
            "sync_on_init": sync_on_init,
            **kwargs
        }

        self.planner = InterviewPlanner(**bot_args)
        self.analyzer = AnswerAnalyzer(**bot_args)
        self.reporter = ReportGenerator(**bot_args)

        # Expose the underlying method for soft-followup compatibility
        self._chat_json = self.analyzer._chat_json
        self.persona = self.planner.persona

        self.rag_ready = self.planner.rag_ready # Check readiness from one of the components

    def design_interview_plan(self) -> Dict:
        return self.planner.design_interview_plan()

    def analyze_answer_with_rag(self, question: str, answer: str, role: Optional[str] = None) -> Dict:
        return self.analyzer.analyze_answer_with_rag(question, answer, role)

    def generate_follow_up_question(self, *args, **kwargs) -> List[str]:
        return self.analyzer.generate_follow_up_question(*args, **kwargs)

    def generate_detailed_final_report(self, *args, **kwargs) -> Dict:
        return self.reporter.generate_detailed_final_report(*args, **kwargs)

    def generate_final_report(self, *args, **kwargs) -> Dict:
        return self.reporter.generate_final_report(*args, **kwargs)

    def print_final_report(self, report: Dict):
        self.reporter.print_final_report(report)

    def print_individual_analysis(self, analysis: Dict, question_num: str):
        self.reporter.print_individual_analysis(analysis, question_num)

    # This method is not part of the original class, but it is used in conduct_interview.
    # For now, I will add it here. It should probably be moved to the planner.
    def analyze_resume_with_rag(self) -> Dict:
        # This logic was not present in the provided file, so I am creating a placeholder.
        print("Analyzing resume with RAG...")
        return {"job_fit_assessment": "Placeholder"}

    def conduct_interview(self):
        if not self.rag_ready:
            print("\n❌ RAG system is not ready. Cannot conduct interview.")
            return

        resume_analysis = self.analyze_resume_with_rag()
        interview_plan_data = self.design_interview_plan()
        if "error" in interview_plan_data and not interview_plan_data.get("interview_plan"):
            print(f"\n❌ {interview_plan_data['error']}")
            return
        plan = interview_plan_data.get("interview_plan")
        if not plan:
            print("\n❌ Could not create an interview plan.")
            return
        interview_plan = plan

        print("\n" + "=" * 70)
        print(f"🏢 Starting interview for {self.planner.company_name} {self.planner.job_title} (Mode: {self.planner.interviewer_mode})")
        print("Type /quit to end the interview.")
        print("=" * 70)

        interview_transcript: List[Dict] = []
        interview_stopped = False

        for i, stage_data in enumerate(interview_plan, 1):
            stage_name = stage_data.get("stage", f"Stage {i}")
            objectives = stage_data.get("objectives") or stage_data.get("objective")
            stage_objective = objectives[0] if isinstance(objectives, list) and objectives else (objectives or "N/A")
            questions = stage_data.get("questions", [])

            print(f"\n\n--- Interview Stage {i}: {stage_name} ---")
            print(f"🎯 Objective: {stage_objective}")

            for q_idx, question in enumerate(questions, 1):
                question_id = f"{i}-{q_idx}"
                print(f"\n--- [Question {question_id}] ---")
                print(f"👨‍💼 Interviewer: {question}")
                answer = input("💬 Answer: ")

                if answer.lower() in ["/quit", "/exit"]:
                    interview_stopped = True
                    break

                analysis = self.analyze_answer_with_rag(question, answer, role=self.planner.job_title)

                fu_list: List[str] = []
                fu_disp = ""
                fu_answer = ""
                if analysis and "error" not in analysis:
                    fu_list = self.generate_follow_up_question(
                        original_question=question,
                        answer=answer,
                        analysis=analysis,
                        stage=stage_name,
                        objective=stage_objective,
                        limit=1 # Limiting to 1 follow-up for CLI
                    )
                    if fu_list:
                        fu_disp = fu_list[0]
                        print("\n--- [Follow-up Question] ---")
                        print(f"👨‍💼 Interviewer: {fu_disp}")
                        fu_answer = input("💬 Answer: ")

                interview_transcript.append({
                    "question_id": question_id,
                    "stage": stage_name,
                    "objective": stage_objective,
                    "question": question,
                    "answer": answer,
                    "analysis": analysis,
                    "follow_up_question": fu_disp,
                    "follow_up_answer": fu_answer
                })

            if interview_stopped:
                break

        print("\n🎉 Interview finished. Thank you for your time.")

        if interview_transcript:
            self._generate_and_print_reports(interview_transcript, interview_plan_data, resume_analysis)

def main():
    try:
        target_container = "interview-data"
        company_name = input("Company Name (e.g., Kia): ")
        safe_company_name_for_index = unidecode((company_name or '').lower()).replace(" ", "-") or "unknown"
        index_name = f"{safe_company_name_for_index}-report-index"
        job_title = input("Job Title (e.g., Production - Operations & Process Engineering): ")
        difficulty = input("Interview Difficulty (easy, normal, hard): ") or "normal"
        interviewer_mode = input("Interviewer Mode (team_lead, executive): ") or "team_lead"

        print("\n" + "-" * 40)
        print(f"Target Container: {target_container}")
        print(f"Company Name: {company_name}")
        print(f"AI Search Index: {index_name}")
        print(f"Difficulty: {difficulty}")
        print(f"Interviewer Mode: {interviewer_mode}")
        print("-" * 40)

        bot = RAGInterviewBot(
            company_name=company_name,
            job_title=job_title,
            container_name=target_container,
            index_name=index_name,
            difficulty=difficulty,
            interviewer_mode=interviewer_mode,
            sync_on_init=False,
        )
        bot.conduct_interview()

    except Exception as e:
        print(f"\n❌ A critical error occurred: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()