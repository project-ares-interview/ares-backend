import json
import traceback

from openai import AzureOpenAI
from unidecode import unidecode

from django.conf import settings

# RAG 시스템 클래스를 임포트합니다.
from .new_azure_rag_llamaindex import AzureBlobRAGSystem
# 웹 검색 도구 임포트
from .tool_code import google_search
# [수정] INTERVIEWER_PERSONAS를 prompt.py에서 직접 임포트합니다.
from ares.api.services.prompt import (
    INTERVIEWER_PERSONAS,
    prompt_interview_designer,
    DIFFICULTY_INSTRUCTIONS,
    prompt_resume_analyzer,
    prompt_rag_answer_analysis,
    prompt_rag_json_correction,
    prompt_rag_follow_up_question,
    prompt_rag_final_report,
)
from ares.api.utils.ai_utils import safe_extract_json


def _debug_print_raw_json(label: str, payload: str):
    """디버깅 편의를 위한 원문 출력(서버 로그에서 확인). 과하게 길면 앞/뒤만."""
    try:
        head = payload[:800]
        tail = payload[-400:] if len(payload) > 1200 else ""
        print(f"\n--- {label} RAW JSON (len={len(payload)}) START ---\n{head}")
        if tail:
            print("\n... (snip) ...\n")
            print(tail)
        print(f"--- {label} RAW JSON END ---\n")
    except Exception:
        pass


class RAGInterviewBot:
    """[최종] 평가 결과를 면접 종료 후 일괄 제공하는 면접 시스템"""

    def __init__(
        self,
        company_name: str,
        job_title: str,
        container_name: str,
        index_name: str,
        difficulty: str = "normal",
        interviewer_mode: str = "team_lead",  # [추가] 면접관 모드 파라미터
        ncs_context: dict | None = None,
        jd_context: str = "",
        resume_context: str = "",
        research_context: str = "",
        **kwargs,
    ):
        print(f"🤖 RAG 전용 사업 분석 면접 시스템 초기화 (면접관: {interviewer_mode})...")
        self.company_name = company_name
        self.job_title = job_title
        self.difficulty = difficulty
        self.interviewer_mode = interviewer_mode  # [추가] 면접관 모드 저장
        self.ncs_context = ncs_context or {}
        self.jd_context = jd_context
        self.resume_context = resume_context
        self.research_context = research_context

        # [추가] 선택된 페르소나 정보를 self.persona에 저장
        self.persona = INTERVIEWER_PERSONAS.get(self.interviewer_mode, INTERVIEWER_PERSONAS["team_lead"])

        # API 정보 로드 (Django settings 사용)
        self.endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", None)
        self.api_key = getattr(settings, "AZURE_OPENAI_KEY", None)
        self.api_version = (
            getattr(settings, "AZURE_OPENAI_API_VERSION", None)
            or getattr(settings, "API_VERSION", None)
            or "2024-08-01-preview"
        )
        self.model = (
            getattr(settings, "AZURE_OPENAI_MODEL", None)
            or getattr(settings, "AZURE_OPENAI_DEPLOYMENT", None)
            or "gpt-4o"
        )

        if not self.endpoint or not self.api_key:
            raise ValueError("Azure OpenAI endpoint/key is not set in Django settings.")

        self.client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version,
        )

        print("\n📊 Azure 사업 분석 RAG 시스템 연동...")
        self.rag_system = None
        self.rag_ready = False
        try:
            self.rag_system = AzureBlobRAGSystem(container_name=container_name, index_name=index_name)

            blobs = list(self.rag_system.container_client.list_blobs())
            if not blobs:
                print(f"⚠️ 경고: Azure Blob 컨테이너 '{container_name}'에 분석할 파일이 없습니다.")
                return

            print(f"✅ Azure RAG 시스템 준비 완료. {len(blobs)}개의 문서를 기반으로 합니다.")
            print("🔄 Azure AI Search 인덱스 자동 동기화 시작...")
            self.rag_system.sync_index(company_name_filter=self.company_name)
            self.rag_ready = True

        except Exception as e:
            print(f"❌ RAG 시스템 연동 실패: {e}")

    def design_interview_plan(self) -> dict:
        """RAG 기반으로 구조화된 면접 계획 생성"""
        if not self.rag_ready:
            return {}
        print(f"\n🧠 {self.company_name} 맞춤 면접 계획 설계 중 (난이도: {self.difficulty}, 면접관: {self.interviewer_mode})...")
        try:
            business_info = self.rag_system.query(
                f"{self.company_name}의 핵심 사업, 최근 실적, 주요 리스크, 그리고 {self.job_title} 직무와 관련된 회사 정보에 대해 요약해줘."
            )

            ncs_info = ""
            if self.ncs_context.get("ncs"):
                ncs_titles = [item.get("title") for item in self.ncs_context["ncs"] if item.get("title")]
                if ncs_titles:
                    ncs_info = f"\n\nNCS 직무 관련 정보: {', '.join(ncs_titles)}."

            difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(self.difficulty, "")

            # [수정] 페르소나 정보를 format에 추가
            prompt = prompt_interview_designer.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                question_style_guide=self.persona["question_style_guide"],
                company_name=self.company_name,
                job_title=self.job_title,
                difficulty_instruction=difficulty_instruction,
                business_info=business_info,
                jd_context=self.jd_context,
                resume_context=self.resume_context,
                research_context=self.research_context,
                ncs_info=ncs_info,
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.8,
            )
            result = safe_extract_json(response.choices[0].message.content)
            print("✅ 구조화 면접 계획 수립 완료.")
            return result
        except Exception as e:
            print(f"❌ 면접 계획 수립 실패: {e}")
            return {}

    def analyze_resume_with_rag(self) -> dict:
        """RAG를 활용하여 이력서와 회사 정보의 연관성 분석"""
        if not self.rag_ready or not self.resume_context:
            return {}
        print(f"\n📄 RAG 기반 이력서 분석 중 (면접관: {self.interviewer_mode})...")
        try:
            business_info = self.rag_system.query(
                f"{self.company_name}의 핵심 사업, 최근 실적, 주요 리스크, 그리고 {self.job_title} 직무와 관련된 회사 정보에 대해 요약해줘."
            )

            # [수정] 페르소나 정보를 format에 추가
            prompt = prompt_resume_analyzer.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                company_name=self.company_name,
                job_title=self.job_title,
                business_info=business_info,
                resume_context=self.resume_context,
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.5,
            )
            result = safe_extract_json(response.choices[0].message.content)
            print("✅ 이력서-회사 연관성 분석 완료.")
            return result
        except Exception as e:
            print(f"❌ 이력서 분석 실패: {e}")
            return {}

    def analyze_answer_with_rag(self, question: str, answer: str) -> dict:
        """개별 답변에 대한 상세 분석 (XAI 기반, 점수 없음)"""
        if not self.rag_ready:
            return {"error": "RAG 시스템 미준비"}

        print(f"     (답변 분석 중... 면접관: {self.interviewer_mode})")

        try:
            web_result = google_search.search(queries=[f"{self.company_name} {answer}"])
            if not isinstance(web_result, str):
                web_result = json.dumps(web_result, ensure_ascii=False)[:2000]
        except Exception:
            web_result = "검색 실패 또는 결과 없음"

        internal_check = self.rag_system.query(
            f"'{answer}'라는 주장에 대한 사실관계를 확인하고 관련 데이터를 찾아줘."
        )

        # [수정] 페르소나 정보를 format에 추가
        analysis_prompt = prompt_rag_answer_analysis.format(
            persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
            evaluation_focus=self.persona["evaluation_focus"],
            company_name=self.company_name,
            question=question,
            answer=answer,
            internal_check=internal_check,
            web_result=web_result,
        )

        raw_json = ""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Respond with ONLY a JSON object that strictly matches the intended structure. No prose, no code fences."},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.2,
                max_tokens=2000,
            )
            raw_json = response.choices[0].message.content or ""

            result = safe_extract_json(raw_json)
            if result is not None:
                return result
            raise json.JSONDecodeError("Initial JSON parsing failed, attempting self-correction", raw_json, 0)

        except json.JSONDecodeError as e:
            _debug_print_raw_json("FIRST_PASS_FAILED", raw_json)
            print(f"⚠️ JSON 파싱 실패 ({e}), AI 자가 교정 시도.")

            correction_prompt = prompt_rag_json_correction
            try:
                correction_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Return ONLY valid JSON. No markdown or commentary."},
                        {"role": "user", "content": analysis_prompt},
                        {"role": "assistant", "content": raw_json},
                        {"role": "user", "content": correction_prompt}
                    ],
                    temperature=0.0,
                    max_tokens=2000,
                )
                corrected_raw = correction_response.choices[0].message.content or ""
                final_result = safe_extract_json(corrected_raw)

                if final_result is not None:
                    return final_result
                _debug_print_raw_json("CORRECTION_PASS_FAILED", corrected_raw)
                raise json.JSONDecodeError("Failed to parse AI response after self-correction", corrected_raw, 0)

            except Exception as e:
                print(f"❌ 답변 분석 최종 실패: {e}")
                traceback.print_exc()
                return {"error": f"Failed to parse AI response: {e}"}

        except Exception as e:
            print(f"❌ 답변 분석 실패 (일반 오류): {e}")
            traceback.print_exc()
            return {"error": f"Failed to analyze answer: {e}"}

    def print_individual_analysis(self, analysis: dict, question_num: str):
        """개별 답변에 대한 분석 결과 출력 형식"""
        if "error" in analysis:
            print(f"\n❌ 분석 오류: {analysis['error']}")
            return

        print("\n" + "=" * 70)
        print(f"📊 [{question_num}] 답변 상세 분석 결과")
        print("=" * 70)

        print("\n" + "-" * 30)
        print("✅ 주장별 사실 확인 (Fact-Checking)")
        fact_checks = analysis.get("fact_checking", [])
        if not fact_checks:
            print("  - 확인된 주장이 없습니다.")
        else:
            for check in fact_checks:
                print(f'  - 주장: "{check.get("claim", "N/A")}"')
                print(f"    - 검증: {check.get('verification', 'N/A')}")
                print(f"    - 근거: {check.get('evidence', 'N/A')}")

        print("\n" + "-" * 30)
        print("📝 내용 분석 (Content Analysis)")
        content = analysis.get("content_analysis", {})
        depth = content.get("analytical_depth", {})
        insight = content.get("strategic_insight", {})
        print(f"  - 데이터 분석 깊이: {depth.get('assessment', 'N/A')}")
        print(f"    - 코멘트: {depth.get('comment', 'N/A')}")
        print(f"  - 전략적 통찰력: {insight.get('assessment', 'N/A')}")
        print(f"    - 코멘트: {insight.get('comment', 'N/A')}")

        print("\n" + "-" * 30)
        print("💡 실행 가능한 피드백 (Actionable Feedback)")
        feedback = analysis.get("actionable_feedback", {})
        strengths = feedback.get("strengths", [])
        suggestions = feedback.get("suggestions_for_improvement", [])
        if strengths:
            print("  - 강점:")
            for s in strengths:
                print(f"    ✓ {s}")
        if suggestions:
            print("  - 개선 제안:")
            for s in suggestions:
                print(f"    -> {s}")
        print("=" * 70)

    def generate_follow_up_question(self, original_question: str, answer: str, analysis: dict, stage: str, objective: str) -> str:
        """분석 결과를 바탕으로 심층 꼬리 질문 생성"""
        try:
            suggestions = analysis.get("actionable_feedback", {}).get("suggestions_for_improvement", [])

            # [수정] 페르소나 정보를 format에 추가
            prompt = prompt_rag_follow_up_question.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                company_name=self.company_name,
                original_question=original_question,
                answer=answer,
                suggestions=", ".join(suggestions),
                stage=stage,
                objective=objective,
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            result = safe_extract_json(response.choices[0].message.content)
            return result.get("follow_up_question", "")
        except Exception as e:
            print(f"❌ 꼬리 질문 생성 실패: {e}")
            return ""

    def conduct_interview(self):
        """구조화된 면접 계획에 따라 면접 진행"""
        if not self.rag_ready:
            print("\n❌ RAG 시스템이 준비되지 않아 면접을 진행할 수 없습니다.")
            return

        # 면접 시작 전, 이력서 분석 선행
        resume_analysis = self.analyze_resume_with_rag()

        interview_plan_data = self.design_interview_plan()
        if not interview_plan_data or "interview_plan" not in interview_plan_data:
            print("\n❌ 면접 계획을 수립하지 못했습니다.")
            return

        interview_plan = interview_plan_data.get("interview_plan", [])

        print("\n" + "=" * 70)
        print(f"🏢 {self.company_name} {self.job_title} 직무 {self.interviewer_mode} 면접을 시작하겠습니다.")
        print("면접은 총 3단계로 구성되며, 각 단계의 질문에 답변해주시면 됩니다.")
        print("면접이 종료된 후 전체 답변에 대한 상세 분석이 제공됩니다.")
        print("=" * 70)

        interview_transcript = []
        question_counter = 0

        for i, stage_data in enumerate(interview_plan, 1):
            stage_name = stage_data.get("stage", f"단계 {i}")
            stage_objective = stage_data.get("objective", "N/A")
            questions = stage_data.get("questions", [])

            print(f"\n\n--- 면접 단계 {i}: {stage_name} ---")
            print(f"🎯 이번 단계의 목표: {stage_objective}")

            for q_idx, question in enumerate(questions, 1):
                question_counter += 1
                question_id = f"{i}-{q_idx}"

                print(f"\n--- [질문 {question_id}] ---")
                print(f"👨‍💼 면접관: {question}")
                answer = input("💬 답변: ")
                if answer.lower() in ["/quit", "/종료"]:
                    break

                analysis = self.analyze_answer_with_rag(question, answer)

                follow_up_question = ""
                follow_up_answer = ""
                if "error" not in analysis:
                    follow_up_question = self.generate_follow_up_question(
                        original_question=question,
                        answer=answer,
                        analysis=analysis,
                        stage=stage_name,
                        objective=stage_objective
                    )
                    if follow_up_question:
                        print("\n--- [꼬리 질문] ---")
                        print(f"👨‍💼 면접관: {follow_up_question}")
                        follow_up_answer = input("💬 답변: ")

                interview_transcript.append({
                    "question_id": question_id,
                    "stage": stage_name,
                    "objective": stage_objective,
                    "question": question,
                    "answer": answer,
                    "analysis": analysis,
                    "follow_up_question": follow_up_question,
                    "follow_up_answer": follow_up_answer
                })

            if answer.lower() in ["/quit", "/종료"]:
                break

        print("\n🎉 면접이 종료되었습니다. 수고하셨습니다.")

        if interview_transcript:
            print("\n\n" + "#" * 70)
            print(" 면접 전체 답변에 대한 상세 분석 리포트")
            print("#" * 70)

            for item in interview_transcript:
                self.print_individual_analysis(item["analysis"], item["question_id"])

            report = self.generate_final_report(interview_transcript, interview_plan_data, resume_analysis)
            self.print_final_report(report)

    def generate_final_report(self, transcript: list, interview_plan: dict, resume_feedback_analysis: dict) -> dict:
        """면접 전체 기록과 계획, 이력서 분석 결과를 바탕으로 최종 종합 리포트 생성"""
        print("\n\n" + "#" * 70)
        print(f" 최종 역량 분석 종합 리포트 생성 중... (면접관: {self.interviewer_mode})")
        print("#" * 70)

        try:
            conversation_summary = ""
            for item in transcript:
                q_id = item.get("question_id", "N/A")
                analysis_assessment = "분석 미완료"
                if isinstance(item.get("analysis"), dict):
                    content_analysis = item["analysis"].get("content_analysis", {})
                    if isinstance(content_analysis, dict):
                         strategic_insight = content_analysis.get("strategic_insight", {})
                         if isinstance(strategic_insight, dict):
                            analysis_assessment = strategic_insight.get("assessment", "분석 미완료")

                conversation_summary += (
                    f"질문 {q_id} ({item.get('stage', 'N/A')}): {item.get('question', '')}\n"
                    f"답변 {q_id}: {item.get('answer', '')}\n"
                    f"(개별 분석 요약: {analysis_assessment})\n---\n"
                )

            # [수정] 페르소나 정보를 format에 추가
            report_prompt = prompt_rag_final_report.format(
                persona_description=self.persona["persona_description"].format(company_name=self.company_name, job_title=self.job_title),
                final_report_goal=self.persona["final_report_goal"],
                company_name=self.company_name,
                job_title=self.job_title,
                conversation_summary=conversation_summary,
                interview_plan=json.dumps(interview_plan, ensure_ascii=False),
                resume_feedback_analysis=json.dumps(resume_feedback_analysis, ensure_ascii=False),
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": report_prompt}],
                temperature=0.3,
                max_tokens=4000,
            )
            report_data = safe_extract_json(response.choices[0].message.content)
            return report_data

        except Exception as e:
            print(f"❌ 최종 리포트 생성 중 오류 발생: {e}")
            traceback.print_exc()
            return {}

    def print_final_report(self, report: dict):
        """최종 종합 리포트 출력"""
        if not report:
            return

        print("\n\n" + "=" * 70)
        print(f"🏅 {self.company_name} {self.job_title} 지원자 최종 역량 분석 종합 리포트 (관점: {self.interviewer_mode})")
        print("=" * 70)

        print("\n■ 면접 계획 달성도 평가\n" + "-" * 50)
        print(report.get("assessment_of_plan_achievement", "평가 정보 없음."))

        print("\n■ 총평 (Overall Summary)\n" + "-" * 50)
        print(report.get("overall_summary", "요약 정보 없음."))

        print("\n■ 핵심 역량 분석 (Core Competency Analysis)\n" + "-" * 50)
        for comp in report.get("core_competency_analysis", []):
            print(f"  - {comp.get('competency', 'N/A')}: **{comp.get('assessment', 'N/A')}**")
            print(f"    - 근거: {comp.get('evidence', 'N/A')}")

        print("\n■ 성장 가능성 (Growth Potential)\n" + "-" * 50)
        print(f"  {report.get('growth_potential', 'N/A')}")

        if "resume_feedback" in report:
            print("\n■ 이력서 피드백 (Resume Feedback)\n" + "-" * 50)
            feedback = report.get("resume_feedback", {})
            if isinstance(feedback, dict):
                print(f"  - 직무 적합성: {feedback.get('job_fit_assessment', 'N/A')}")
                print(f"  - 강점 및 기회: {feedback.get('strengths_and_opportunities', 'N/A')}")
                print(f"  - 개선점: {feedback.get('gaps_and_improvements', 'N/A')}")
            else:
                 print(f"  {feedback}")

        if "question_by_question_feedback" in report:
            print("\n■ 질문별 상세 피드백 (Question-by-Question Feedback)\n" + "-" * 50)
            for item in report.get("question_by_question_feedback", []):
                print(f"  - 질문: {item.get('question', 'N/A')}")
                print(f"    - 질문 의도: {item.get('question_intent', 'N/A')}")
                evaluation = item.get("evaluation", {})
                if isinstance(evaluation, dict):
                    print(f"    - 적용된 프레임워크: {evaluation.get('applied_framework', 'N/A')}")
                    print(f"    - 피드백: {evaluation.get('feedback', 'N/A')}")
                else:
                    print(f"    - 피드백: {evaluation}")
                print("    " + "-" * 20)

        print("\n" + "=" * 70)


def main():
    try:
        target_container = "interview-data"
        company_name = input("면접을 진행할 회사 이름 (예: 기아): ")
        safe_company_name_for_index = unidecode(company_name.lower()).replace(" ", "-")
        index_name = f"{safe_company_name_for_index}-report-index"
        job_title = input("지원 직무 (예: 생산 - 생산운영 및 공정기술): ")
        difficulty = input("면접 난이도 (easy, normal, hard): ")
        # [추가] main 함수에서 면접관 모드를 입력받음
        interviewer_mode = input("면접관 모드 (team_lead, executive): ")

        print("\n" + "-" * 40)
        print(f"대상 컨테이너: {target_container}")
        print(f"회사 이름: {company_name}")
        print(f"AI Search 인덱스: {index_name}")
        print(f"난이도: {difficulty}")
        print(f"면접관 모드: {interviewer_mode}") # [추가]
        print("-" * 40)

        # [수정] RAGInterviewBot 생성 시 interviewer_mode 전달
        bot = RAGInterviewBot(
            company_name=company_name,
            job_title=job_title,
            container_name=target_container,
            index_name=index_name,
            difficulty=difficulty,
            interviewer_mode=interviewer_mode
        )
        bot.conduct_interview()

    except Exception as e:
        print(f"\n❌ 시스템 실행 중 심각한 오류 발생: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
