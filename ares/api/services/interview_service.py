# ares/api/services/interview_service.py
# =========================================================
# 면접 질문/꼬리질문/STAR-C 평가 + (선택) NCS 컨텍스트 주입
# - ncs_query 비었을 때 meta.role/division/company로 자동 대체
# - NCS 모듈 및 함수 callable 가드
# - CLI 테스트 진입점 / 로그 디버그 토글
# =========================================================

from __future__ import annotations

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import time, re, json, argparse, sys

from ares.api.utils.ai_utils import chat
from ares.api.utils.common_utils import get_logger

# 🔎 NCS 하이브리드 검색/컨텍스트 주입 (선택)
#   - env: AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY / NCS_INDEX 필요
try:
    from ares.api.utils import search_utils as ncs  # 모듈 단일 import
except Exception:
    ncs = None

_log = get_logger("interview")

__all__ = [
    "make_outline",
    "generate_main_question_ondemand",
    "generate_followups",
    "score_answer_starc",
]

# =========================
# 설정/유틸
# =========================
@dataclass
class GenConfig:
    temperature_outline: float = 0.4
    temperature_main: float = 0.5
    temperature_follow: float = 0.3
    temperature_score: float = 0.2

    max_tokens_outline: int = 220
    max_tokens_main: int = 160
    max_tokens_follow: int = 260
    max_tokens_score: int = 520

    context_max_chars: int = 10000
    answer_max_chars: int = 6000

    ncs_top_outline: int = 6
    ncs_top_main: int = 6
    ncs_top_follow: int = 4
    ncs_top_score: int = 4
    ncs_ctx_max_len: int = 1800

    # 신규: 실무 편의
    max_follow_k: int = 8          # 꼬리질문 1회 최대 생성 개수
    debug_log_prompts: bool = False  # 대용량 프롬프트 로깅 토글

CFG = GenConfig()

def _safe_strip(s: str) -> str:
    return (s or "").strip()

def _normalize_lines(text: str) -> List[str]:
    lines = []
    for raw in (text or "").splitlines():
        l = raw.strip()
        if not l:
            continue
        l = re.sub(r"^[\-\•\d\.\)\(]+\s*", "", l)
        if l:
            lines.append(l)
    return lines

def _dedup_preserve_order(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\s+", " ", it).strip().lower()
        if key and key not in seen:
            seen.add(key); out.append(it)
    return out

def _too_similar(a: str, b: str, thresh: float = 0.6) -> bool:
    ta = set(re.findall(r"[가-힣A-Za-z0-9]+", (a or "").lower()))
    tb = set(re.findall(r"[가-힣A-Za-z0-9]+", (b or "").lower()))
    if not ta or not tb:
        return False
    inter, union = len(ta & tb), len(ta | tb)
    return (inter / max(1, union)) >= thresh

def _not_too_long(s: str, max_chars: int) -> str:
    s = s or ""
    return s if len(s) <= max_chars else s[:max_chars]

def _first_sentence(s: str) -> str:
    """여러 줄/여러 문장일 때 첫 문장만."""
    s = _safe_strip(s)
    # 줄 기준 우선
    s = s.splitlines()[0] if "\n" in s else s
    # 문장 종결부 기준(물음표/마침표)로 1문장만
    m = re.search(r"(.+?[\.?!？])(\s|$)", s)
    return m.group(1).strip() if m else s

def _ensure_question_mark(s: str) -> str:
    s = _safe_strip(s)
    return s if s.endswith("?") or s.endswith("？") else (s + "?") if s else s

def _safe_chat(
    msgs: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    fallback: str = "",
    retries: int = 2,
    backoff: float = 0.8
) -> str:
    last_err = None
    for i in range(retries + 1):
        try:
            out = chat(msgs, temperature=temperature, max_tokens=max_tokens)
            return out or fallback
        except Exception as e:
            last_err = e
            _log.warning(f"chat() 실패, 재시도 {i}/{retries}: {e}")
            time.sleep(backoff * (2 ** i))
    _log.error(f"chat() 최종 실패: {last_err}")
    return fallback

# =========================
# (선택) NCS 컨텍스트 주입
# =========================
def _resolve_ncs_query(ncs_query: Optional[str], meta: Optional[dict]) -> str:
    q = (ncs_query or "").strip()
    if not q and meta:
        # role → division → company 순으로 대체
        q = (meta.get("role") or meta.get("division") or meta.get("company") or "").strip()
    # 공백뿐이면 무시
    return q if q else ""

def _build_ncs_ctx(query: Optional[str], top: int, max_len: int) -> str:
    """
    NCS 인덱스가 있을 때만 컨텍스트 문자열 생성. 실패/미설정이면 빈 문자열.
    """
    if not ncs:
        return ""
    if not (hasattr(ncs, "search_ncs_hybrid") and callable(getattr(ncs, "search_ncs_hybrid"))):
        return ""
    if not (hasattr(ncs, "format_ncs_context") and callable(getattr(ncs, "format_ncs_context"))):
        return ""

    q = (query or "").strip()
    if not q:
        return ""

    try:
        hits = ncs.search_ncs_hybrid(q, top=top)
        ctx = ncs.format_ncs_context(hits, max_len=max_len) or ""
        _log.info(f"NCS 컨텍스트: hits={len(hits)}, query='{q[:60]}'")
        return ctx
    except Exception as e:
        _log.warning(f"NCS 컨텍스트 생성 실패: {e}")
        return ""

# =========================
# 전문화 프롬프트 (SYS)
# =========================
SYS_OUTLINE = (
    "너는 Fortune 500 제조·IT 기업의 시니어 면접관이다. "
    "컨텍스트를 바탕으로 면접 '섹션 아웃라인'만 작성한다. "
    "규칙: (1) 불릿/번호 금지 (2) 한 줄에 하나 (3) 8~24자 (4) 중복·유사 금지. "
    "제조/설비/반도체 컨텍스트면 OEE, TPM, MTBF/MTTR, FDC/예지보전 고려."
)

SYS_MAIN_Q = (
    "너는 대기업 기술직 면접관이다. 새로운 주제의 '메인 질문' 1개만 작성한다. "
    "제약: (1) 이미 한 질문과 중복 금지 (2) 한국어 한 문장 (3) 끝은 물음표 (4) 70자 이내. "
    "난이도: 쉬움=경험 개요, 보통=역할·결과 수치, 어려움=가설/리스크/사후학습. "
    "제조/설비/반도체면 OEE/TPM/MTBF/MTTR/불량률/가동률·FDC/예지보전 지표 고려."
)

SYS_FOLLOW = (
    "너는 집요한 시니어 면접관이다. 메인 질문·답변을 바탕으로 '파고드는 꼬리질문' k개를 만든다. "
    "카테고리 분산: [지표/수치], [본인역할/의사결정], [리스크/대안], [협업/갈등], [학습/회고]. "
    "규칙: (1) 한국어 한 문장 (2) 60자 이내 (3) 중복 금지 (4) '수치/기간/범위' 포함 시도. "
    "금지어: '열심히', '많이', '최대한', '중요했다'."
)

SYS_STARC = (
    "너는 시니어 면접관이다. STAR-C(상황·과제·행동·결과·성찰)로 평가한다. "
    "JSON만 출력. 다른 텍스트 금지.\n"
    '{ "scores":{"S":0-5,"T":0-5,"A":0-5,"R":0-5,"C":0-5}, '
    '"weighted_total":number, "grade":"A|B|C|D", '
    '"comments":{"S":"","T":"","A":"","R":"","C":""}, '
    '"summary":["- 강점 ...","- 보완점 ...","- 추가 제안 ..."] }\n'
    "A≥22.5, B≥18.0, C≥13.0, else D."
)

# =========================
# 메타 주입
# =========================
def _inject_company_ctx(prompt: str, meta: dict | None) -> str:
    if not meta:
        return prompt
    def _s(x): return (x or "").strip()
    comp = _s(meta.get("company",""))
    div  = _s(meta.get("division",""))
    role = _s(meta.get("role",""))
    loc  = _s(meta.get("location",""))
    kpis = ", ".join([_s(x) for x in meta.get("jd_kpis",[]) if _s(x)])[:200]
    skills = ", ".join([_s(x) for x in meta.get("skills",[]) if _s(x)])[:200]
    ctx = (f"[회사 컨텍스트]\n"
           f"- 회사: {comp or '미상'} | 부서/직무: {div or '-'} / {role or '-'} | 근무지: {loc or '-'}\n"
           f"- KPI: {kpis or '-'} | 스킬: {skills or '-'}\n\n")
    return ctx + prompt

# =========================
# USR 빌더 (+ NCS 컨텍스트)
# =========================
def _outline_usr(context: str, n: int, meta: dict | None, ncs_ctx: str) -> str:
    p = (f"[컨텍스트]\n{_not_too_long(context, CFG.context_max_chars)}\n\n")
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += (f"요구사항:\n- 섹션 {n}개\n- 한국어, 불릿 없음\n- 각 줄 8~24자, 명사형 위주\n"
          "출력: 섹션명만 줄바꿈으로 나열")
    return _inject_company_ctx(p, meta)

def _main_usr(context: str, prev: List[str], difficulty: str, meta: dict | None, ncs_ctx: str) -> str:
    prev_block = "\n".join([f"- {q}" for q in (prev or [])]) or "- (없음)"
    p = (f"[컨텍스트]\n{_not_too_long(context, 8000)}\n\n")
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += (f"[이미 한 질문]\n{prev_block}\n\n"
          f"[난이도]\n{difficulty}\n\n"
          "출력: 메인 질문 한 문장만(70자 이내, 끝은 물음표). 중복/유사 금지.")
    return _inject_company_ctx(p, meta)

def _follow_usr(main_q: str, answer: str, k: int, meta: dict | None, ncs_ctx: str) -> str:
    p = (f"[메인 질문]\n{_safe_strip(main_q)}\n\n"
         f"[지원자 답변]\n{_not_too_long(_safe_strip(answer), CFG.answer_max_chars)}\n\n")
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += (f"요구: 꼬리질문 {k}개, 서로 다른 카테고리에서 생성.\n"
          "출력: 줄바꿈으로 질문만 나열")
    return _inject_company_ctx(p, meta)

def _starc_usr(q: str, a: str, meta: dict | None, ncs_ctx: str) -> str:
    p = (f"[질문]\n{_safe_strip(q)}\n\n"
         f"[답변]\n{_safe_strip(a)}\n\n")
    if ncs_ctx:
        p += f"[NCS 컨텍스트]\n{ncs_ctx}\n\n"
    p += "출력: JSON만."
    return _inject_company_ctx(p, meta)

# =========================
# 1) 섹션 아웃라인
# =========================
def make_outline(context: str, n: int = 5, meta: dict | None = None, ncs_query: str | None = None) -> List[str]:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG.ncs_top_outline, CFG.ncs_ctx_max_len)
    msgs = [
        {"role": "system", "content": SYS_OUTLINE},
        {"role": "user", "content": _outline_usr(context, n, meta, ncs_ctx)},
    ]
    if CFG.debug_log_prompts:
        try:
            _log.debug("=== make_outline prompt ===\n" + json.dumps(msgs, ensure_ascii=False, indent=2))
        except Exception:
            pass

    out = _safe_chat(
        msgs,
        temperature=CFG.temperature_outline,
        max_tokens=CFG.max_tokens_outline,
        fallback=""
    )
    lines = _dedup_preserve_order(_normalize_lines(out))
    if not lines:
        lines = ["문제해결", "협업", "품질", "리스크", "고객집착"]
    return lines[:n]

# =========================
# 2) 메인 질문 생성 (온디맨드 1개)
# =========================
def generate_main_question_ondemand(
    context: str,
    prev_questions: List[str],
    difficulty: str = "보통",
    meta: dict | None = None,
    ncs_query: str | None = None
) -> str:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG.ncs_top_main, CFG.ncs_ctx_max_len)
    msgs = [
        {"role": "system", "content": SYS_MAIN_Q},
        {"role": "user", "content": _main_usr(context, prev_questions, difficulty, meta, ncs_ctx)},
    ]
    if CFG.debug_log_prompts:
        try:
            _log.debug("=== generate_main_question_ondemand prompt ===\n" + json.dumps(msgs, ensure_ascii=False, indent=2))
        except Exception:
            pass

    out = _safe_chat(
        msgs,
        temperature=CFG.temperature_main,
        max_tokens=CFG.max_tokens_main,
        fallback="해당 직무 관련 핵심 경험을 한 가지 사례로 설명해 주시겠습니까?"
    )
    q = _first_sentence(out)
    # 유사성 체크
    for pq in prev_questions or []:
        if _too_similar(q, pq):
            q = "이전 질문과 겹치지 않는 다른 핵심 경험을 한 가지 선택해 구체적으로 설명해 주시겠습니까?"
            break
    q = _ensure_question_mark(q)
    return q

# =========================
# 3) 꼬리질문 생성 (하위호환 지원)
# =========================
def generate_followups(
    main_q: Optional[str] = None,
    answer: Optional[str] = None,
    k: int = 3,
    main_index: Optional[int] = None,
    meta: Optional[dict] = None,
    ncs_query: Optional[str] = None,
    **legacy_kwargs,  # ← 예전 호출 방식(language, difficulty, ncs_context, based_on_answer, modes 등)
) -> List[str]:
    """
    하위호환 인자 매핑:
      - based_on_answer -> answer
      - ncs_context(list[dict] or list[str]) -> ncs_ctx 문자열로 병합
      - modes -> 카테고리 힌트(현재는 다양성 확보용으로만 사용)
      - language/difficulty -> 프롬프트 튜닝 힌트(현재는 강제 규칙은 아님)
    """
    # k 가드
    if k <= 0:
        return []
    if k > CFG.max_follow_k:
        k = CFG.max_follow_k

    # ---- 하위호환 매핑 ----
    if answer is None and "based_on_answer" in legacy_kwargs:
        answer = legacy_kwargs.get("based_on_answer") or ""

    # 예전 코드가 ncs_context(리스트/딕트들)를 직접 넣어줄 때를 지원
    legacy_ncs_ctx = legacy_kwargs.get("ncs_context")
    ncs_ctx_from_list = ""
    if legacy_ncs_ctx:
        try:
            # 문자열 리스트
            if isinstance(legacy_ncs_ctx, list) and all(isinstance(x, str) for x in legacy_ncs_ctx):
                ncs_ctx_from_list = "\n".join(f"- {x}" for x in legacy_ncs_ctx if x.strip())
            # 딕트 리스트: {"code","title","desc"} 형태
            elif isinstance(legacy_ncs_ctx, list) and all(isinstance(x, dict) for x in legacy_ncs_ctx):
                buf = []
                for it in legacy_ncs_ctx:
                    code = (it.get("code") or it.get("ncs_code") or "").strip()
                    title = (it.get("title") or it.get("ncs_title") or "").strip()
                    desc = (it.get("desc") or it.get("summary") or it.get("description") or "").strip()
                    line = " / ".join([x for x in [code, title, desc] if x])
                    if line:
                        buf.append(f"- {line}")
                ncs_ctx_from_list = "\n".join(buf)
        except Exception:
            ncs_ctx_from_list = ""

    # modes 힌트(현재는 다양성만 유도)
    modes_hint = legacy_kwargs.get("modes") or []
    if isinstance(modes_hint, (list, tuple)):
        modes_hint = [str(m).strip() for m in modes_hint if str(m).strip()]
    else:
        modes_hint = []

    # 언어/난이도 힌트 (필요시 튜닝용으로 사용 가능)
    lang_hint = (legacy_kwargs.get("language") or "").lower().strip()
    diff_hint = (legacy_kwargs.get("difficulty") or "").strip()

    # ---- NCS 컨텍스트 조립(신규 + 하위호환 병합) ----
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx_from_search = _build_ncs_ctx(ncs_query, CFG.ncs_top_follow, CFG.ncs_ctx_max_len)
    # 우선순위: 검색 컨텍스트 + (있으면) 호출자가 직접 준 리스트 컨텍스트
    if ncs_ctx_from_search and ncs_ctx_from_list:
        ncs_ctx = ncs_ctx_from_search + "\n" + ncs_ctx_from_list
    else:
        ncs_ctx = ncs_ctx_from_search or ncs_ctx_from_list

    # 메인 질문 없을 때 방어적 기본값
    if not main_q:
        main_q = "이전 주제에 대해 더 깊이 파고들기 위한 추가 질문을 생성해 주세요."

    # 프롬프트 구성
    sys_prompt = SYS_FOLLOW
    if lang_hint == "en":
        # 영어로도 쓸 수 있게 아주 가볍게 전환(선택)
        sys_prompt = sys_prompt.replace("한국어", "영어")

    user_prompt = _follow_usr(main_q, answer or "", k, meta, ncs_ctx)
    if modes_hint:
        user_prompt += "\n[카테고리 힌트]\n- " + ", ".join(modes_hint)

    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if CFG.debug_log_prompts:
        try:
            _log.debug("=== generate_followups prompt ===\n" + json.dumps(msgs, ensure_ascii=False, indent=2))
        except Exception:
            pass

    out = _safe_chat(
        msgs,
        temperature=CFG.temperature_follow,
        max_tokens=CFG.max_tokens_follow,
        fallback=""
    )
    lines = _dedup_preserve_order(_normalize_lines(out))
    if not lines:
        lines = [
            "핵심 지표와 기준선/기간을 수치로 명확히 제시해 주시겠어요?",
            "본인 고유 의사결정과 선택 근거를 구체적으로 설명해 주시겠어요?",
            "주요 리스크와 대비 대안(플랜B/C)은 무엇이었나요?"
        ][:k]
    lines = lines[:k]

    if main_index is not None:
        prefix = str(int(main_index))
        lines = [f"{prefix}-{i+1}. {q.strip()}" for i, q in enumerate(lines)]
    return lines


# =========================
# 4) STAR-C 평가 (가중합/등급 포함)
# =========================
def score_answer_starc(
    q: str,
    a: str,
    meta: dict | None = None,
    ncs_query: str | None = None
) -> Dict[str, Any]:
    ncs_query = _resolve_ncs_query(ncs_query, meta)
    ncs_ctx = _build_ncs_ctx(ncs_query, CFG.ncs_top_score, CFG.ncs_ctx_max_len)
    msgs = [
        {"role": "system", "content": SYS_STARC},
        {"role": "user", "content": _starc_usr(q, a, meta, ncs_ctx)},
    ]
    if CFG.debug_log_prompts:
        try:
            _log.debug("=== score_answer_starc prompt ===\n" + json.dumps(msgs, ensure_ascii=False, indent=2))
        except Exception:
            pass

    raw = _safe_chat(
        msgs,
        temperature=CFG.temperature_score,
        max_tokens=CFG.max_tokens_score,
        fallback=""
    ).strip()

    result: Dict[str, Any] = {
        "scores": {}, "weighted_total": None, "grade": None,
        "comments": {}, "summary": []
    }
    try:
        data = json.loads(raw)
        if isinstance(data.get("scores"), dict):
            result["scores"] = data["scores"]
        if "weighted_total" in data:
            result["weighted_total"] = data["weighted_total"]
        if "grade" in data:
            result["grade"] = data["grade"]
        if isinstance(data.get("comments"), dict):
            result["comments"] = data["comments"]
        if isinstance(data.get("summary"), list):
            result["summary"] = data["summary"]

        # 가중합 없으면 계산
        if result["scores"] and result["weighted_total"] is None:
            S = float(result["scores"].get("S", 0))
            T = float(result["scores"].get("T", 0))
            A = float(result["scores"].get("A", 0))
            R = float(result["scores"].get("R", 0))
            C = float(result["scores"].get("C", 0))
            weighted = S*1.0 + T*1.0 + A*1.2 + R*1.2 + C*0.8
            result["weighted_total"] = round(weighted, 2)

        # 등급 없으면 산정
        if result["grade"] is None and result["weighted_total"] is not None:
            wt = result["weighted_total"]
            if wt >= 22.5: grade = "A"
            elif wt >= 18.0: grade = "B"
            elif wt >= 13.0: grade = "C"
            else: grade = "D"
            result["grade"] = grade

        if not result["summary"]:
            result["summary"] = [
                "- 강점: 핵심 KPI/역할 일부 제시",
                "- 보완점: 수치/기간/규모 구체화 부족",
                "- 추가 제안: 결과-원인 연결 강화 및 사후 학습 계획 명시"
            ]
    except Exception as e:
        _log.warning(f"STAR-C JSON 파싱 실패: {e} | raw={raw[:800]}")
        result["summary"] = [raw or "평가 생성 실패"]

    return result

# =========================
# CLI 테스트 진입점
# =========================
def _cli():
    p = argparse.ArgumentParser(description="Interview service quick test")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_out = sub.add_parser("outline")
    p_out.add_argument("--ctx", required=True)
    p_out.add_argument("--n", type=int, default=5)

    p_main = sub.add_parser("mainq")
    p_main.add_argument("--ctx", required=True)
    p_main.add_argument("--prev", default="")
    p_main.add_argument("--difficulty", default="보통")

    p_follow = sub.add_parser("follow")
    p_follow.add_argument("--mainq", required=True)
    p_follow.add_argument("--answer", required=True)
    p_follow.add_argument("--k", type=int, default=3)
    p_follow.add_argument("--index", type=int)

    p_score = sub.add_parser("score")
    p_score.add_argument("--q", required=True)
    p_score.add_argument("--a", required=True)

    args = p.parse_args()
    meta = {}  # 필요 시 metadata_service.build_meta_from_inputs로 구성

    if args.cmd == "outline":
        print("\n".join(make_outline(args.ctx, n=args.n, meta=meta)))
    elif args.cmd == "mainq":
        prev = [x.strip() for x in args.prev.split("||") if x.strip()]
        print(generate_main_question_ondemand(args.ctx, prev, difficulty=args.difficulty, meta=meta))
    elif args.cmd == "follow":
        print("\n".join(generate_followups(args.mainq, args.answer, k=args.k, main_index=args.index, meta=meta)))
    elif args.cmd == "score":
        print(json.dumps(score_answer_starc(args.q, args.a, meta=meta), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    try:
        _cli()
    except Exception as e:
        _log.error(f"interview_service CLI 실패: {e}")
        sys.exit(1)
