import os
import json
import re
import pandas as pd
from openai import AzureOpenAI
from django.conf import settings

from .prompt import (
    prompt_identifier,
    prompt_extractor,
    prompt_scorer,
    prompt_coach,
    prompt_model_answer,
    prompt_first_interview_question,
)
from .scoring import _BASE_KEYS, _SIGNAL_KEYS_MAP
from .ncs_retriever import AzureNCSRetriever


def extract_json_from_response(text: str) -> str:
    """AI의 응답 텍스트에서 순수한 JSON 부분만 추출합니다."""
    match = re.search(r'```json\s*(\{.*\})\s*```', text, re.DOTALL)
    if match:
        return match.group(1)

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)

    return text


class InterviewBot:
    def __init__(self, job_title, company_name, company_description):
        self.job_title = job_title
        self.company_name = company_name
        self.company_description = company_description

        self.client = AzureOpenAI(
            azure_endpoint=getattr(settings, "AZURE_OPENAI_ENDPOINT", None),
            api_key=getattr(settings, "AZURE_OPENAI_API_KEY", None),
            api_version=getattr(settings, "API_VERSION", "2024-02-15-preview"),
        )
        self.model = getattr(settings, "AZURE_OPENAI_MODEL", "gpt-35-turbo")
        self.ncs_retriever = AzureNCSRetriever()
        self.conversation_history = []

        print(f"✅ InterviewBot 인스턴스 생성 완료: {self.company_name} - {self.job_title}")

    def ask_first_question(self) -> str:
        """첫 번째 질문을 생성하여 '반환'합니다."""
        interviewer_prompt = prompt_first_interview_question.format(
            company_name=self.company_name,
            job_title=self.job_title,
            company_description=self.company_description
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": interviewer_prompt},
                {"role": "user", "content": "첫 면접 질문을 해주세요."},
            ],
            max_tokens=300,
            temperature=0.8,
        )
        question = response.choices[0].message.content.strip()
        self.conversation_history.append({'question': question, 'answer': None})
        return question

    def analyze_answer(self, question: str, answer: str) -> dict:
        """MCP 방식으로 답변을 분석합니다."""
        if self.conversation_history:
            self.conversation_history[-1]["answer"] = answer

        insufficient_feedback = {
            "scores": {},
            "strengths": ["(분석 불가)"],
            "improvements": [
                "답변의 내용이 너무 짧거나 추상적이어서 AI가 분석할 수 없었습니다.",
                "면접관의 질문 의도를 파악하고, 구체적인 사례나 전략을 포함하여 답변해 주세요.",
                "예를 들어, STAR나 CASE 프레임워크를 활용하여 답변을 구조화하는 것이 좋습니다.",
            ],
            "feedback": "답변이 너무 추상적입니다. 구체적으로 답변해주세요.",
            "model_answer": "(분석 불가)",
        }

        if len(answer) < 20:
            return insufficient_feedback

        try:
            final_result = {}
            raw_responses = {}

            # 1단계: Identifier
            identifier_messages = [
                {"role": "system", "content": prompt_identifier},
                {"role": "user", "content": answer},
            ]
            response1 = self.client.chat.completions.create(
                model=self.model, messages=identifier_messages, max_tokens=200, temperature=0.0
            )
            raw_responses["identifier"] = response1.choices[0].message.content
            cleaned_str1 = extract_json_from_response(raw_responses["identifier"])
            identified_data = json.loads(cleaned_str1)

            frameworks_to_process = identified_data.get("frameworks", [])
            if not frameworks_to_process:
                return insufficient_feedback

            final_result["selected_framework_answerer"] = frameworks_to_process
            analysis_summaries = {}
            all_scores = {}
            scoring_reasons = []

            retrieved_ncs_details = self.ncs_retriever.search(self.job_title)

            # 2 & 3단계: Extractor & Scorer
            for fw_token in frameworks_to_process:
                parts = fw_token.lower().split('+')
                base_fw = parts[0].strip()
                signal = parts[1] if len(parts) > 1 else None

                component_list = _BASE_KEYS.get(base_fw, [])
                if signal and signal in _SIGNAL_KEYS_MAP:
                    component_list.append(_SIGNAL_KEYS_MAP[signal])
                if not component_list:
                    continue
                component_list_str = "\n- ".join(component_list)

                # Extractor
                analysis_key_map = {
                    "star": "star_analysis",
                    "competency": "base_analysis",
                    "case": "case_analysis",
                    "systemdesign": "system_analysis",
                }
                analysis_key = analysis_key_map.get(base_fw, f"{base_fw}_analysis")

                extractor_messages = [
                    {
                        "role": "system",
                        "content": prompt_extractor.format(
                            framework_name=base_fw,
                            analysis_key=analysis_key,
                            component_list=component_list_str,
                        ),
                    },
                    {"role": "user", "content": answer},
                ]
                response2 = self.client.chat.completions.create(
                    model=self.model, messages=extractor_messages, max_tokens=1500, temperature=0.1
                )
                raw_responses[f"extractor_{base_fw}"] = response2.choices[0].message.content
                cleaned_str2 = extract_json_from_response(raw_responses[f"extractor_{base_fw}"])
                extracted_data = json.loads(cleaned_str2)
                analysis_summaries.update(extracted_data)

                # Scorer
                scorer_user_prompt = f"### 분석할 요약 내용:\n{json.dumps(extracted_data, ensure_ascii=False, indent=2)}"
                scorer_messages = [
                    {
                        "role": "system",
                        "content": prompt_scorer.format(
                            framework_name=base_fw,
                            company=self.company_name,
                            description=self.company_description,
                            retrieved_ncs_details=retrieved_ncs_details,
                            component_list=component_list_str,
                            role=self.job_title,
                        ),
                    },
                    {"role": "user", "content": scorer_user_prompt},
                ]
                response3 = self.client.chat.completions.create(
                    model=self.model, messages=scorer_messages, max_tokens=1000, temperature=0.0
                )
                raw_responses[f"scorer_{base_fw}"] = response3.choices[0].message.content
                cleaned_str3 = extract_json_from_response(raw_responses[f"scorer_{base_fw}"])
                scored_data = json.loads(cleaned_str3)
                all_scores.update(scored_data.get("scores", {}))
                if "scoring_reason" in scored_data:
                    scoring_reasons.append(scored_data["scoring_reason"])

            final_result.update(analysis_summaries)
            final_result["scores"] = all_scores
            final_result["scoring_reason"] = "\n".join(scoring_reasons)

            # Framework scores
            framework_scores = {}
            for fw_token in frameworks_to_process:
                parts = fw_token.lower().split('+')
                base_fw = parts[0].strip()
                signal = parts[1].strip() if len(parts) > 1 else None

                base_keys = _BASE_KEYS.get(base_fw, [])
                signal_key = _SIGNAL_KEYS_MAP.get(signal, None)

                total_score = sum(all_scores.get(key, 0) for key in base_keys)
                max_score = len(base_keys) * 20

                if signal_key and signal_key in all_scores:
                    total_score += all_scores.get(signal_key, 0)
                    max_score += 10

                if max_score > 0:
                    framework_scores[fw_token.upper()] = {
                        "total": total_score,
                        "max": max_score,
                    }

            final_result["framework_scores"] = framework_scores

            # 4단계: Coach
            coach_input = f"### 분석 데이터:\n{json.dumps(final_result, ensure_ascii=False, indent=2)}"
            coach_messages = [
                {
                    "role": "system",
                    "content": prompt_coach.format(
                        role=self.job_title, retrieved_ncs_details=retrieved_ncs_details
                    ),
                },
                {"role": "user", "content": coach_input},
            ]
            response4 = self.client.chat.completions.create(
                model=self.model, messages=coach_messages, max_tokens=1500, temperature=0.7
            )
            raw_responses["coach"] = response4.choices[0].message.content
            cleaned_str4 = extract_json_from_response(raw_responses["coach"])
            feedback_json = json.loads(cleaned_str4)
            final_result.update(feedback_json)

            # 5단계: Role Model
            model_answer_input = f"### 면접 질문:\n{question}\n\n### 지원자의 답변:\n{answer}\n\n### 코치의 개선점 피드백:\n{', '.join(feedback_json.get('improvements', []))}"
            model_answer_messages = [
                {
                    "role": "system",
                    "content": prompt_model_answer.format(
                        role=self.job_title,
                        retrieved_ncs_details=retrieved_ncs_details,
                        description=self.company_description,
                    ),
                },
                {"role": "user", "content": model_answer_input},
            ]
            response5 = self.client.chat.completions.create(
                model=self.model, messages=model_answer_messages, max_tokens=1500, temperature=0.5
            )
            raw_responses["model_answer"] = response5.choices[0].message.content
            cleaned_str5 = extract_json_from_response(raw_responses["model_answer"])
            model_answer_json = json.loads(cleaned_str5)
            final_result.update(model_answer_json)

            return final_result

        except Exception as e:
            print(f"🔴 분석 중 치명적인 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            return {"error": f"분석 중 오류가 발생했습니다: {e}"}
