# ares/api/services/resume_service.py
from __future__ import annotations
from typing import Dict
from ares.api.utils.ai_utils import chat, get_client  # ← 패키지 경로로 통일

def analyze_resume_or_cover(text: str, jd_text: str = "") -> str:
    sys = ("채용담당자+커리어코치 관점. 문서를 JD 기준으로 매칭도/핵심키워드/정량화/STAR/성과지표/기술스택/ATS 리스크 "
           "진단 후 수정안 제시. 한국어 섹션/불릿.")
    usr = (f"[문서]\n{text[:15000]}\n\n[선택적 JD]\n{(jd_text or '')[:8000]}\n\n"
           "요구 출력: 1) 요약 2) JD 매칭도(상중하+근거) 3) 키워드 커버리지 표 "
           "4) STAR 사례 5) 정량화 개선안 6) ATS 리스크/수정안 7) 최종 체크리스트")
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.2, max_tokens=1200)

def compare_documents(named_texts: Dict[str, str]) -> str:
    joined = "\n\n".join([f"[{k}]\n{v[:8000]}" for k, v in named_texts.items()])
    sys = ("채용담당자 관점. 이력서/자소서 간 일관성, 수치·기간·역할 모순, 누락 스토리라인을 찾고 정렬 가이드(수정 예문) 제시.")
    usr = joined + "\n\n요구 출력: 1) 일관/모순 표 2) 누락된 연결고리 3) 정렬 가이드(수정 예문)."
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.2, max_tokens=1200)

def analyze_research_alignment(jd_text: str, doc_text: str, research_text: str) -> str:
    sys = ("너는 채용담당자다. JD, 지원서, 지원자 리서치 간의 일관성/모순/누락/근거성을 점검하고, "
           "자소서/면접에서 보강할 포인트를 제시하라. 한국어 섹션/불릿.")
    usr = (f"[JD]\n{jd_text[:6000]}\n\n[지원서]\n{doc_text[:6000]}\n\n[리서치]\n{research_text[:8000]}\n\n"
           "요구 출력: 1) 핵심 정합도 요약 2) 일관/모순/누락 표 3) 근거성/최신성 리스크와 대안 4) 보강 문장 예시")
    return chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], temperature=0.25, max_tokens=1100)

if __name__ == "__main__":
    import argparse, sys
    # 테스트 실행도 패키지 경로로 통일
    from ares.api.services.resume_service import analyze_resume_or_cover, compare_documents, analyze_research_alignment

    p = argparse.ArgumentParser(description="Resume/Cover analysis test")
    p.add_argument("--mode", choices=["deep","cmp","align"], default="deep")
    p.add_argument("--doc", default="공정개선 성과 15% 향상, 설비 보전 PM/TPM 수행", help="deep용 문서")
    p.add_argument("--jd", default="설비 유지보수, 예지보전, 공정 최적화", help="deep/align용 JD")
    p.add_argument("--doc2", default="자소서: TPM 활동으로 불량률 2%p 개선", help="cmp용 문서2")
    p.add_argument("--research", default="동종사 대비 OEE/MTBF 지표", help="align용 리서치")
    args = p.parse_args()

    if not get_client():
        print("Azure OpenAI 환경변수 설정 필요")
        sys.exit(1)

    if args.mode == "deep":
        print(analyze_resume_or_cover(args.doc, args.jd))
    elif args.mode == "cmp":
        print(compare_documents({"doc1": args.doc, "doc2": args.doc2}))
    elif args.mode == "align":
        print(analyze_research_alignment(args.jd, args.doc, args.research))
