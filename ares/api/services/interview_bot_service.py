# ares/api/services/interview_bot_service.py
import os
import re
import json
import pandas as pd
from datetime import datetime

from openai import AzureOpenAI
from django.conf import settings

# ARES project imports
from ares.api.services.blob_storage import BlobStorage
from ares.api.services.prompt import (
    prompt_identifier, prompt_extractor, prompt_scorer,
    prompt_coach, prompt_model_answer
)
from ares.api.services.scoring import _BASE_KEYS, _SIGNAL_KEYS_MAP

# RAG/NCS search utils for context injection
try:
    from ares.api.utils import search_utils as ncs
except ImportError:
    ncs = None


def extract_json_from_response(text: str) -> str:
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m: return m.group(1)
    m = re.search(r'\{.*?\}', text, re.DOTALL)
    if m: return m.group(0)
    return text

def safe_json_loads(text: str, default=None):
    """모델 응답에서 JSON 부분만 뽑아 안전하게 loads"""
    try:
        return json.loads(text)
    except Exception:
        try:
            cleaned = extract_json_from_response(text)
            return json.loads(cleaned)
        except Exception:
            return default if default is not None else {}

# 🔹 Lazy-loading for the company DataFrame
COMPANY_DF = None

def get_company_df():
    """Lazily loads the company DataFrame and caches it."""
    global COMPANY_DF
    if COMPANY_DF is None:
        try:
            bs = BlobStorage()
            df = bs.read_csv("companies_updated.csv")
            COMPANY_DF = df
        except Exception as e:
            print(f"Could not read companies_updated.csv from blob storage: {e}")
            local_path = os.path.join(settings.BASE_DIR, "data", "companies_updated.csv")
            if os.path.exists(local_path):
                COMPANY_DF = pd.read_csv(local_path)
            else:
                COMPANY_DF = pd.DataFrame()  # Assign empty df on failure
    return COMPANY_DF


class InterviewBot:
    def __init__(self, company_name, job_title, model=None):
        self.company_keyword = company_name
        self.job_title = job_title

        # ⚠️ settings에 실제 배포 이름과 API 버전이 있어야 함
        self.endpoint = getattr(settings, 'AZURE_OPENAI_ENDPOINT', '')
        self.api_key = getattr(settings, 'AZURE_OPENAI_KEY', '')
        self.api_version = getattr(settings, 'API_VERSION', '2024-08-01-preview')
        self.model = model or getattr(settings, 'AZURE_OPENAI_MODEL', 'gpt-4o-mini')  # ← 기본값 최신으로 권장

        if not self.endpoint or not self.api_key:
            raise ValueError("Azure OpenAI endpoint/key is not set in Django settings.")

        self.client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version
        )

        company_df = get_company_df()
        company_description = "요청된 회사에 대한 인재상 정보를 찾을 수 없습니다."
        if not company_df.empty and 'company_name' in company_df.columns:
            matching = company_df[
                company_df['company_name'].astype(str).str.contains(self.company_keyword, case=False, na=False)
            ]
            if not matching.empty:
                row = matching.iloc[0]
                desc = row.get('detailed_description', '')
                company_description = str(desc).strip() if str(desc).strip() else "해당 계열사의 인재상 정보가 비어 있습니다."

        self.company_description = company_description
        self.conversation_history = []

    def ask_question(self) -> str:
        interviewer_prompt = (
            f"너는 {self.company_keyword} 회사의 채용 면접관이야.\n"
            f"나를 면접대상자로 간주하고 {self.job_title} 직무에 관련된 면접 질문을 한 개만 해줘.\n"
            f"아래 인재상/설명도 참고해서 질문해줘:\n\n"
            f"{self.company_description}\n"
        )
        try:
            # ✅ 이중 중괄호 제거, 올바른 리스트/딕셔너리
            messages = [{"role": "system", "content": interviewer_prompt}]
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=300,
                temperature=0.8
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return f"질문 생성 오류: {e}"

    def analyze_answer(self, question: str, answer: str, ncs_query: str = None) -> dict:
        rag_context = ""
        if ncs and ncs_query:
            try:
                hits = ncs.search_ncs_hybrid(ncs_query, top=3)
                rag_context = ncs.format_ncs_context(hits, max_len=4000)
            except Exception as e:
                print(f"NCS context generation failed: {e}")

        try:
            final_result = {}

            # 1) Identifier
            id_msgs = [
                {"role": "system", "content": prompt_identifier.format(description=self.company_description)},
                {"role": "user", "content": answer},
            ]
            r1 = self.client.chat.completions.create(
                model=self.model,
                messages=id_msgs,
                max_tokens=200,
                temperature=0.0,
            )
            identified = safe_json_loads(r1.choices[0].message.content, default={})
            frameworks = identified.get("frameworks", [])
            if not frameworks:
                return {"error": "프레임워크 식별 실패: 결과 없음"}
            final_result["selected_framework_answerer"] = frameworks

            summaries, scores_all, reasons = {}, {}, []

            for fw_token in frameworks:
                parts = fw_token.lower().split('+')
                base = parts[0].strip()
                signal = parts[1].strip() if len(parts) > 1 else None

                comp_list = list(_BASE_KEYS.get(base, []))
                if signal and signal in _SIGNAL_KEYS_MAP:
                    comp_list.append(_SIGNAL_KEYS_MAP[signal])
                if not comp_list:
                    continue
                comp_str = "\n- ".join(comp_list)

                # 2) Extractor
                key_map = {
                    "star": "star_analysis",
                    "competency": "base_analysis",
                    "case": "case_analysis",
                    "systemdesign": "system_analysis",
                }
                analysis_key = key_map.get(base, f"{base}_analysis")
                ext_msgs = [
                    {"role": "system", "content": prompt_extractor.format(
                        framework_name=base,
                        analysis_key=analysis_key,
                        component_list=comp_str
                    )},
                    {"role": "user", "content": answer},
                ]
                r2 = self.client.chat.completions.create(
                    model=self.model,
                    messages=ext_msgs,
                    max_tokens=1500,
                    temperature=0.1,
                )
                cleaned2 = safe_json_loads(r2.choices[0].message.content, default={})
                summaries.update(cleaned2)

                # 3) Scorer (with RAG context)
                scorer_user = f"### 분석할 요약 내용:\n{json.dumps(cleaned2, ensure_ascii=False, indent=2)}"
                scorer_system_prompt = prompt_scorer.format(
                    framework_name=base,
                    role=self.job_title,
                    retrieved_ncs_details=rag_context
                )
                sc_msgs = [
                    {"role": "system", "content": scorer_system_prompt},
                    {"role": "user", "content": scorer_user},
                ]
                r3 = self.client.chat.completions.create(
                    model=self.model,
                    messages=sc_msgs,
                    max_tokens=1000,
                    temperature=0.0,
                )
                cleaned3 = safe_json_loads(r3.choices[0].message.content, default={})
                scores_all.update((cleaned3.get("scores") or {}))
                if "scoring_reason" in cleaned3 and cleaned3["scoring_reason"]:
                    reasons.append(cleaned3["scoring_reason"])

            final_result.update(summaries)
            final_result["scores"] = scores_all
            final_result["scoring_reason"] = "\n".join(reasons)

            # 4) Coach (with RAG context)
            coach_input = f"### 분석 데이터:\n{json.dumps(final_result, ensure_ascii=False, indent=2)}"
            coach_system_prompt = prompt_coach.format(
                role=self.job_title,
                retrieved_ncs_details=rag_context
            )
            r4 = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": coach_system_prompt},
                          {"role": "user", "content": coach_input}],
                max_tokens=1500,
                temperature=0.7,
            )
            feedback = safe_json_loads(r4.choices[0].message.content, default={})
            final_result.update(feedback)

            # 5) Role Model (with RAG context)
            improvements = feedback.get('improvements') or []
            model_in = (
                f"### 면접 질문:\n{question}\n\n"
                f"### 지원자의 답변:\n{answer}\n\n"
                f"### 코치의 개선점:\n{', '.join(improvements)}"
            )
            model_answer_system_prompt = prompt_model_answer.format(
                role=self.job_title,
                description=self.company_description,
                retrieved_ncs_details=rag_context
            )
            r5 = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": model_answer_system_prompt},
                          {"role": "user", "content": model_in}],
                max_tokens=1500,
                temperature=0.5,
            )
            final_result.update(safe_json_loads(r5.choices[0].message.content, default={}))

            return final_result

        except Exception as e:
            print(f"Error during answer analysis: {e}")
            return {"error": f"분석 중 오류: {e}"}
