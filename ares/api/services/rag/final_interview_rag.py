import os
import json
import sys
from openai import AzureOpenAI
from unidecode import unidecode
import re
import traceback

# RAG 시스템 클래스를 임포트합니다.
from .new_azure_rag_llamaindex import AzureBlobRAGSystem
# 웹 검색 도구 임포트
from .tool_code import google_search


def _sanitize_json_object(text: str) -> str:
    """모델이 섞어 보낸 마크다운/스마트쿼트/누락 쉼표 등을 정리해 JSON을 강제로 정상화."""
    # 코드펜스/백틱 제거
    text = re.sub(r"```(?:json)?", "", text).replace("```", "")
    # 스마트 쿼트 -> ASCII
    text = (
        text
        .replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
    )
    # 가장 바깥 {}만 남기기 (단순 그리디)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)

    # 줄바꿈 경계에서 누락된 쉼표 보정: ...}\n"key" → ...},\n"key"
    text = re.sub(r'([}\]0-9eE"\\])\s*[\r\n]+\s*(")', r"\1,\n\2", text)

    # } "key" 처럼 공백만 있고 콤마 없는 경우: } "key" → }"key"
    text = re.sub(r'([}\]])\s*(")', r'\1\2', text)

    # 트레일링 콤마 제거: , } 또는 , ] → } 또는 ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # True/False/None → true/false/null (파이썬 표기 보정)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text.strip()


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


def extract_json_from_response(text: str) -> str:
    """AI의 응답 텍스트에서 순수한 JSON 부분만 추출."""
    # 1) 코드펜스 내 JSON 우선
    m = re.search(r'```json\s*(\{.*\})\s*```', text, re.DOTALL)
    if m:
        return m.group(1)
    # 2) 텍스트에서 가장 큰 JSON 객체(그리디)
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        return m.group(0)
    # 3) 원문 반환 (최후의 수단)
    return text


class RAGInterviewBot:
    """[최종] 평가 결과를 면접 종료 후 일괄 제공하는 면접 시스템"""

    def __init__(self, company_name: str, job_title: str, container_name: str, index_name: str):
        print("🤖 RAG 전용 사업 분석 면접 시스템 초기화...")
        self.company_name = company_name
        self.job_title = job_title

        # API 버전 키 정합성: AZURE_OPENAI_API_VERSION 우선, 없으면 API_VERSION 폴백
        api_version = (
            os.getenv('AZURE_OPENAI_API_VERSION')
            or os.getenv('API_VERSION')
            or '2024-08-01-preview'
        )

        self.client = AzureOpenAI(
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            api_key=os.getenv('AZURE_OPENAI_KEY'),
            api_version=api_version,
        )
        # 배포/모델명 키 호환: MODEL → DEPLOYMENT → 기본값
        self.model = (
            os.getenv('AZURE_OPENAI_MODEL')
            or os.getenv('AZURE_OPENAI_DEPLOYMENT')
            or 'gpt-4o'
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

    def generate_questions(self, num_questions: int = 3) -> list:
        """RAG 기반으로 사업 현황 심층 질문 생성"""
        if not self.rag_ready:
            return []
        print(f"\n🧠 {self.company_name} 맞춤 질문 생성 중...")
        try:
            business_info = self.rag_system.query(
                f"{self.company_name}의 핵심 사업, 최근 실적, 주요 리스크에 대해 요약해줘."
            )

            prompt = f"""
당신은 {self.company_name}의 {self.job_title} 직무 면접관입니다.
아래의 최신 사업 현황 데이터를 바탕으로, 지원자의 분석력과 전략적 사고를 검증할 수 있는 날카로운 질문 {num_questions}개를 생성해주세요.
반드시 JSON만 반환하세요.

[최신 사업 요약]
{business_info}

예시 형식:
{{ "questions": ["생성된 질문 1", "생성된 질문 2"] }}
            """

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.8,
            )
            result = json.loads(extract_json_from_response(response.choices[0].message.content))
            questions = result.get("questions", [])
            print(f"✅ {len(questions)}개의 맞춤 질문 생성 완료.")
            return questions
        except Exception as e:
            print(f"❌ 질문 생성 실패: {e}")
            return [
                f"{self.company_name}의 주요 경쟁사와 비교했을 때, 우리 회사가 가진 핵심적인 기술적 우위는 무엇이라고 생각하십니까?"
            ]

    def analyze_answer_with_rag(self, question: str, answer: str) -> dict:
        """개별 답변에 대한 상세 분석 (XAI 기반, 점수 없음)"""
        if not self.rag_ready:
            return {"error": "RAG 시스템 미준비"}

        print("    (답변 분석 중...)")

        # 외부 검색 결과를 문자열로 안전 변환
        try:
            web_result = google_search.search(queries=[f"{self.company_name} {answer}"])
            if not isinstance(web_result, str):
                web_result = json.dumps(web_result, ensure_ascii=False)[:2000]
        except Exception:
            web_result = "검색 실패 또는 결과 없음"

        internal_check = self.rag_system.query(
            f"'{answer}'라는 주장에 대한 사실관계를 확인하고 관련 데이터를 찾아줘."
        )

        analysis_prompt = f"""
당신은 시니어 사업 분석가입니다. 아래 자료를 종합하여 지원자의 답변을 상세히 평가해주세요.
'데이터 기반 사실 분석'과 '독창적인 전략적 통찰력'을 구분하여 평가하고, 점수 대신 서술형으로 평가 의견을 제시하세요.

면접 질문: {question}
지원자 답변: {answer}
---
[자료 1] 내부 사업 데이터: {internal_check}
[자료 2] 외부 웹 검색 결과: {web_result}
---
평가 지침:
1) 주장별 사실 확인: 지원자의 핵심 주장을 1~2개 뽑아 자료 1, 2를 바탕으로 검증합니다.
2) 내용 분석: 데이터 활용 능력과 독창적인 비즈니스 논리를 평가합니다.
3) 피드백: 강점과 개선 제안을 서술합니다.
        """

        # JSON 스키마(안내용, response_format 미사용)
        schema = {
            "name": "answer_analysis",
            "schema": {
                "type": "object",
                "properties": {
                    "fact_checking": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string"},
                                "verification": {"type": "string"},
                                "evidence": {"type": "string"}
                            },
                            "required": ["claim", "verification", "evidence"],
                            "additionalProperties": False
                        }
                    },
                    "content_analysis": {
                        "type": "object",
                        "properties": {
                            "analytical_depth": {
                                "type": "object",
                                "properties": {
                                    "assessment": {"type": "string"},
                                    "comment": {"type": "string"}
                                },
                                "required": ["assessment", "comment"],
                                "additionalProperties": False
                            },
                            "strategic_insight": {
                                "type": "object",
                                "properties": {
                                    "assessment": {"type": "string"},
                                    "comment": {"type": "string"}
                                },
                                "required": ["assessment", "comment"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["analytical_depth", "strategic_insight"],
                        "additionalProperties": False
                    },
                    "actionable_feedback": {
                        "type": "object",
                        "properties": {
                            "strengths": {"type": "array", "items": {"type": "string"}},
                            "suggestions_for_improvement": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["strengths", "suggestions_for_improvement"],
                        "additionalProperties": False
                    }
                },
                "required": ["fact_checking", "content_analysis", "actionable_feedback"],
                "additionalProperties": False
            },
            "strict": True
        }

        raw_json = ""
        try:
            # 1차: JSON 형태 유도
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
            # 일부 SDK는 dict로 줄 수 있음
            if isinstance(raw_json, dict):
                return raw_json

            # 1단계: 그대로 파싱
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError:
                # 1.5단계: 정규화 후 재시도
                sanitized = _sanitize_json_object(raw_json)
                return json.loads(sanitized)

        except json.JSONDecodeError as e:
            _debug_print_raw_json("FIRST_PASS", raw_json)
            print(f"⚠️ JSON 파싱 실패, AI 자가 교정 시도. 오류: {e}")

            correction_prompt = (
                "The previous output did not parse as JSON. Return ONLY a JSON object. "
                "Do not include code fences, markdown, or any explanation. Fix any missing commas or quotes."
            )

            try:
                # 2차: 자가 교정
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
                if isinstance(corrected_raw, dict):
                    return corrected_raw
                try:
                    return json.loads(corrected_raw)
                except json.JSONDecodeError:
                    sanitized = _sanitize_json_object(corrected_raw)
                    return json.loads(sanitized)

            except Exception as final_e:
                _debug_print_raw_json("CORRECTION_PASS", raw_json)
                print(f"❌ 답변 분석 최종 실패 (수정 후에도 오류): {final_e}")
                return {"error": f"Failed to parse AI response after self-correction: {final_e}"}

        except Exception as e:
            _debug_print_raw_json("UNEXPECTED_ERROR", raw_json)
            print(f"❌ 답변 분석 중 오류: {e}")
            return {"error": str(e)}

    def print_individual_analysis(self, analysis: dict, question_num: int):
        """개별 답변에 대한 분석 결과 출력 형식"""
        if "error" in analysis:
            print(f"\n❌ 분석 오류: {analysis['error']}")
            return

        print("\n" + "=" * 70)
        print(f"📊 [질문 {question_num}] 답변 상세 분석 결과")
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

    def generate_follow_up_question(self, original_question: str, answer: str, analysis: dict) -> str:
        """분석 결과를 바탕으로 심층 꼬리 질문 생성"""
        try:
            suggestions = analysis.get("actionable_feedback", {}).get("suggestions_for_improvement", [])
            prompt = (
                "기존 질문: " + original_question + "\n"
                "지원자 답변: " + answer + "\n"
                "답변에 대한 AI 분석 내용(개선 제안): " + ", ".join(suggestions) + "\n\n"
                "위 상황을 바탕으로, 지원자의 논리를 더 깊게 파고들기 위한 핵심 꼬리 질문 1개만 "
                "JSON 형식으로 생성해주세요. (예: {\"follow_up_question\": \"생성된 꼬리 질문\"})"
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            result = json.loads(extract_json_from_response(response.choices[0].message.content))
            return result.get("follow_up_question", "")
        except Exception as e:
            print(f"❌ 꼬리 질문 생성 실패: {e}")
            return ""

    def conduct_interview(self):
        """[수정] 평가 결과는 면접 종료 후 일괄 출력"""
        if not self.rag_ready:
            print("\n❌ RAG 시스템이 준비되지 않아 면접을 진행할 수 없습니다.")
            return

        questions = self.generate_questions()
        if not questions:
            print("\n❌ 면접 질문을 생성하지 못했습니다.")
            return

        print("\n" + "=" * 70)
        print(f"🏢 {self.company_name} {self.job_title} 직무 면접을 시작하겠습니다.")
        print("면접이 종료된 후 전체 답변에 대한 상세 분석이 제공됩니다.")
        print("=" * 70)

        interview_transcript = []

        for i, question in enumerate(questions, 1):
            print(f"\n--- [질문 {i}/{len(questions)}] ---")
            print(f"👨‍💼 면접관: {question}")
            answer = input("💬 답변: ")
            if answer.lower() in ['/quit', '/종료']:
                break

            # [핵심] 평가는 수행하되, 결과는 출력하지 않고 저장만 함
            analysis = self.analyze_answer_with_rag(question, answer)

            follow_up_question = ""
            follow_up_answer = ""
            if "error" not in analysis:
                follow_up_question = self.generate_follow_up_question(question, answer, analysis)
                if follow_up_question:
                    print(f"\n--- [꼬리 질문] ---")
                    print(f"👨‍💼 면접관: {follow_up_question}")
                    follow_up_answer = input("💬 답변: ")

            # 현재 질문, 답변, 분석 내용, 꼬리 질문/답변을 모두 기록
            interview_transcript.append({
                "question_num": i,
                "question": question,
                "answer": answer,
                "analysis": analysis,
                "follow_up_question": follow_up_question,
                "follow_up_answer": follow_up_answer
            })

        print("\n🎉 면접이 종료되었습니다. 수고하셨습니다.")

        # [핵심] 면접 종료 후, 저장된 모든 분석 결과를 일괄 출력
        if interview_transcript:
            print("\n\n" + "#" * 70)
            print(" 면접 전체 답변에 대한 상세 분석 리포트")
            print("#" * 70)

            # 1. 개별 답변 분석 결과부터 순서대로 출력
            for item in interview_transcript:
                self.print_individual_analysis(item['analysis'], item['question_num'])

            # 2. 최종 종합 리포트 생성 및 출력 (누락 보완)
            report = self.generate_final_report(interview_transcript)
            self.print_final_report(report)

    def generate_final_report(self, transcript: list, resume_context: str = "") -> dict:
        """면접 전체 기록을 바탕으로 최종 종합 리포트 생성"""
        print("\n\n" + "#" * 70)
        print(" 최종 역량 분석 종합 리포트 생성 중...")
        print("#" * 70)

        try:
            # 면접 전체 대화 내용과 개별 분석 결과를 요약하여 프롬프트에 전달
            conversation_summary = ""
            for item in transcript:
                q_num = item['question_num']
                analysis_assessment = (
                    item['analysis']
                    .get('content_analysis', {})
                    .get('strategic_insight', {})
                    .get('assessment', '분석 미완료')
                    if isinstance(item.get('analysis'), dict) else '분석 미완료'
                )
                conversation_summary += (
                    f"질문 {q_num}: {item['question']}\n"
                    f"답변 {q_num}: {item['answer']}\n"
                    f"(개별 분석 요약: {analysis_assessment})\n---\n"
                )

            report_prompt = f"""
당신은 시니어 채용 전문가입니다. 아래의 전체 면접 대화 및 개별 분석 요약을 종합하고, 제공된 이력서 내용을 바탕으로 지원자에 대한 '최종 역량 분석 종합 리포트'를 작성해주세요.

[자료] 면접 전체 요약:
{conversation_summary}
---
[자료] 지원자 이력서 내용:
{resume_context if resume_context else "제공된 이력서 내용 없음."}---
리포트 작성 지침:
1) 종합 총평: 지원자의 일관성, 강점, 약점을 종합하여 최종 평가를 내립니다.
2) 핵심 역량 분석: {self.job_title} 직무에 필요한 핵심 역량(예: 문제 해결 능력, 비즈니스 이해도, 기술 전문성) 3가지를 식별하고, 면접 전체 내용을 근거로 [최상], [상], [중], [하]로 평가합니다. 각 평가에 대한 구체적인 근거를 제시해야 합니다.
3) 성장 가능성: 면접 과정에서 보인 태도나 답변의 깊이를 바탕으로 지원자의 잠재력을 평가합니다.
4) 이력서 피드백: 제공된 이력서 내용을 바탕으로, 직무 적합성, 강점, 개선점 등에 대한 피드백을 제공합니다.

응답 형식(JSON만 반환):
{{
  "overall_summary": "종합적인 평가 요약...",
  "core_competency_analysis": [
    {{"competency": "핵심 역량 1", "assessment": "[평가 등급]", "evidence": "판단 근거..."}}
  ],
  "growth_potential": "지원자의 성장 가능성에 대한 코멘트...",
  "resume_feedback": "이력서 내용에 대한 피드백..."
}}
            """

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": report_prompt}],
                temperature=0.3,
                max_tokens=3000,
            )
            report_data = json.loads(extract_json_from_response(response.choices[0].message.content))
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
        print(f"🏅 {self.company_name} {self.job_title} 지원자 최종 역량 분석 종합 리포트")
        print("=" * 70)

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
            print(f"  {report.get('resume_feedback', 'N/A')}")
        print("\n" + "=" * 70)


def main():
    try:
        target_container = 'interview-data'
        company_name = input("면접을 진행할 회사 이름 (예: SK하이닉스): ")
        safe_company_name_for_index = unidecode(company_name.lower()).replace(' ', '-')
        index_name = f"{safe_company_name_for_index}-report-index"
        job_title = input("지원 직무 (예: 사업분석가): ")

        print("\n" + "-" * 40)
        print(f"대상 컨테이너: {target_container}")
        print(f"회사 이름: {company_name}")
        print(f"AI Search 인덱스: {index_name}")
        print("-" * 40)

        bot = RAGInterviewBot(
            company_name=company_name,
            job_title=job_title,
            container_name=target_container,
            index_name=index_name
        )
        bot.conduct_interview()

    except Exception as e:
        print(f"\n❌ 시스템 실행 중 심각한 오류 발생: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
