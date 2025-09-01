# services/interview_service.py
from __future__ import annotations
from typing import List, Dict
from ares.api.utils.ai_utils import chat

def make_outline(context: str, n: int = 5) -> List[str]:
    sys = ("너는 면접관이다. 컨텍스트를 바탕으로 면접 '섹션 아웃라인'만 만든다. "
           "중복 없이 핵심 역량 주제를 간결히 나열하라.")
    usr = (f"[컨텍스트]\n{context[:10000]}\n\n섹션 {n}개를 역량명만 리스트로. 각 줄 하나.")
    out = chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.4, max_tokens=200)
    lines = [l.strip("-• \t") for l in out.splitlines() if l.strip()]
    return lines[:n] if lines else ["문제해결", "협업", "품질", "리스크", "고객집착"][:n]

def generate_main_question_ondemand(context: str, prev_questions: List[str], difficulty: str = "보통") -> str:
    sys = ("너는 면접관이다. 컨텍스트에서 '새로운 주제'의 메인 질문 1개만 만든다. 이미 한 질문과 주제 중복 금지. 한 문장.")
    prev_block = "\n".join(f"- {q}" for q in prev_questions[-6:]) or "- (없음)"
    usr = (f"[컨텍스트]\n{context[:12000]}\n\n[이미 한 질문]\n{prev_block}\n\n[난이도]\n{difficulty}\n\n출력은 질문 한 문장만.")
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.7, max_tokens=200)

def question_for_section(context: str, section: str, prev_questions: List[str], difficulty: str = "보통") -> str:
    sys = ("너는 면접관이다. 주어진 섹션(역량)에 맞춰 메인 질문 1개. 과거 질문과 주제 중복 금지. 한 문장.")
    prev_block = "\n".join(f"- {q}" for q in prev_questions[-6:]) or "- (없음)"
    usr = (f"[컨텍스트]\n{context[:10000]}\n\n[섹션]\n{section}\n\n[이미 한 질문]\n{prev_block}\n\n[난이도]\n{difficulty}\n\n출력은 질문 한 문장만.")
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.6, max_tokens=200)

def generate_followups(q: str, a: str, mode: str = "evidence") -> List[str]:
    direction = {
        "evidence": "증거/수치/지표를 요구",
        "why": "의사결정/선택의 이유를 추궁",
        "how": "구체적 수행과정/단계/재현가능성을 추궁",
        "risk": "리스크/실패사례/한계와 대처를 추궁",
    }.get(mode, "증거/수치/지표를 요구")
    sys = ("너는 면접관이다. '꼬리질문'만 3개 이내로. 직전 답변만 근거로 파고들며 새 주제 금지. 각 줄 하나, 반드시 '?'로 끝낼 것.")
    usr = (f"[이전 질문]\n{q}\n\n[지원자 답변]\n{a}\n\n[질문 방향]\n{direction}\n\n꼬리질문 나열.")
    out = chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.2, max_tokens=220)
    return [l.strip("-• \t") for l in out.splitlines() if l.strip()]

def analyze_answer_star_c(q: str, a: str) -> str:
    sys = ("면접관 시점. STAR+C 각 항목을 0~20점으로 채점하고, 총점 100점. "
           "항목별 간단 코멘트 포함. 한국어. 반드시 아래 포맷으로만 출력.")
    usr = (
        f"[질문]\n{q}\n\n[답변]\n{a}\n\n"
        "포맷:\n"
        "# STAR+C 피드백\n"
        "- Situation: <코멘트> (점수: xx/20)\n"
        "- Task: <코멘트> (점수: xx/20)\n"
        "- Action: <코멘트> (점수: xx/20)\n"
        "- Result: <코멘트> (점수: xx/20)\n"
        "- Clarity: <코멘트> (점수: xx/20)\n"
        "- 총점: yy/100\n"
        "- 종합 피드백: <3~5줄>\n"
        "- 개선 체크리스트:\n"
        "  - <불릿1>\n"
        "  - <불릿2>\n"
        "  - <불릿3>\n"
    )
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.25, max_tokens=900)

def generate_company_specific_questions(context: str, research_text: str, prev_questions: list, difficulty: str = "보통") -> str:
    sys = ("너는 면접관이다. 면접 컨텍스트와 지원자 리서치를 고려해 해당 기업/산업/직무 특화 '메인 질문' 1개만 생성하라. 중복 금지, 한 문장.")
    prev_block = "\n".join(f"- {q}" for q in prev_questions[-6:]) or "- (없음)"
    usr = (f"[면접 컨텍스트]\n{context[:11000]}\n\n[지원자 리서치]\n{research_text[:8000]}\n\n[이미 한 질문]\n{prev_block}\n\n"
           f"[난이도]\n{difficulty}\n\n출력은 질문 한 문장만.")
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.65, max_tokens=180)

if __name__ == "__main__":
    import argparse, sys
    from ares.api.utils.ai_utils import chat, get_client
    from ares.api.services.interview_service import (
        make_outline, generate_main_question_ondemand, question_for_section,
        generate_followups, analyze_answer_star_c
    )

    p = argparse.ArgumentParser(description="Interview service quick test")
    p.add_argument("--context", "-c", default="JD: 설비 유지보수/TPM\n이력서: 이상탐지 PoC", help="면접 컨텍스트")
    p.add_argument("--mode", choices=["outline","main","section","followups","starc"], default="main")
    p.add_argument("--section", default="문제해결", help="mode=section일 때 섹션명")
    p.add_argument("--question", default="TPM 활동에서 당신의 역할은?", help="mode=followups/starc 입력 질문")
    p.add_argument("--answer", default="현장 라인에서 OEE를 8%p 개선...", help="mode=followups/starc 입력 답변")
    args = p.parse_args()

    if not get_client():
        print("Azure OpenAI 환경변수 설정 필요")
        sys.exit(1)

    if args.mode == "outline":
        print(make_outline(args.context, n=5))
    elif args.mode == "main":
        print(generate_main_question_ondemand(args.context, [], "보통"))
    elif args.mode == "section":
        print(question_for_section(args.context, args.section, [], "보통"))
    elif args.mode == "followups":
        print("\n".join(generate_followups(args.question, args.answer, "evidence")))
    elif args.mode == "starc":
        print(analyze_answer_star_c(args.question, args.answer))
