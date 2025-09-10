# utils/state_utils.py
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

# ---------------------------------
# 내부 공용
# ---------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def _cap_str(s: str, limit: int) -> str:
    if limit <= 0:
        return s or ""
    s = s or ""
    return s if len(s) <= limit else (s[:limit])

# ---------------------------------
# 외부 공개 API
# ---------------------------------
def history_labels(history: List[Dict[str, Any]]) -> List[str]:
    return [h.get("id", "") for h in (history or [])]

def ensure_plan(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    세션 계획 기본값 주입 + 가드레일(최대치) 셋업
    """
    plan = dict(plan or {})
    # UX/전략
    plan.setdefault("mode", "혼합형(추천)")
    plan.setdefault("difficulty", "보통")
    plan.setdefault("outline", [])
    plan.setdefault("cursor", 0)

    # 질문/뱅크
    plan.setdefault("question_bank", [])
    plan.setdefault("bank_cursor", 0)

    # 번호/인덱스
    plan.setdefault("main_idx", 0)
    plan.setdefault("follow_idx", 0)

    # 팔로업 정책
    plan.setdefault("follow_per_main", 2)  # 메인당 권장 팔로업 수
    plan.setdefault("max_follow", 6)       # 세션 전체 팔로업 상한

    # 상한(안전 가드)
    plan.setdefault("max_mains", 20)       # 메인 질문 최대 개수
    plan.setdefault("max_turns", 120)      # 전체 턴(메인+팔로업) 상한
    plan.setdefault("max_chars_per_field", 12000)  # q/a/feedback 단일 필드 상한

    return plan

def add_main_turn(history: List[Dict[str, Any]], plan: Dict[str, Any], q_text: str) -> str:
    """
    메인 질문 추가. 인덱스/팔로업 인덱스 리셋 + 상한 검사
    """
    plan = ensure_plan(plan)

    # 상한 검사
    mains_so_far = sum(1 for h in (history or []) if h.get("type") == "main")
    if mains_so_far >= int(plan.get("max_mains", 20)):
        raise ValueError("메인 질문 최대치에 도달했습니다.")

    if len(history or []) >= int(plan.get("max_turns", 120)):
        raise ValueError("세션 최대 턴 수를 초과할 수 없습니다.")

    plan["main_idx"] += 1
    plan["follow_idx"] = 0

    qid = f"{plan['main_idx']}"
    max_chars = int(plan.get("max_chars_per_field", 12000))

    turn = {
        "id": qid,
        "type": "main",
        "q": _cap_str(q_text, max_chars),
        "a": "",
        "feedback": "",
        "created_at": _now(),
        "meta": {"source": "generated", "main_idx": plan["main_idx"], "follow_idx": 0},
        "followups": [],
    }
    history.append(turn)
    return qid

def add_follow_turn(history: List[Dict[str, Any]], plan: Dict[str, Any], q_text: str) -> str:
    """
    팔로업 질문 추가. 현재 main에 종속된 번호(1-1, 1-2 …) 부여 + 상한 검사
    """
    plan = ensure_plan(plan)

    # 활성 메인 유효성
    if plan.get("main_idx", 0) <= 0:
        raise ValueError("먼저 메인 질문을 추가하세요.")

    # 세션 전체 팔로업 상한
    total_follow = sum(1 for h in (history or []) if h.get("type") == "follow")
    if total_follow >= int(plan.get("max_follow", 6)):
        raise ValueError("팔로업 질문 최대치에 도달했습니다.")

    # 메인당 권장 팔로업 제한(권장치지만 여기서는 강제 가드로 적용)
    per_main_limit = int(plan.get("follow_per_main", 2))
    current_main_id = str(plan["main_idx"])
    per_main_count = sum(
        1 for h in (history or []) if h.get("type") == "follow" and str(h.get("id", "")).startswith(current_main_id + "-")
    )
    if per_main_count >= per_main_limit:
        raise ValueError(f"이 메인 질문({current_main_id})에 대한 팔로업은 최대 {per_main_limit}개입니다.")

    # 전체 턴 상한
    if len(history or []) >= int(plan.get("max_turns", 120)):
        raise ValueError("세션 최대 턴 수를 초과할 수 없습니다.")

    plan["follow_idx"] += 1
    qid = f"{plan['main_idx']}-{plan['follow_idx']}"
    max_chars = int(plan.get("max_chars_per_field", 12000))

    turn = {
        "id": qid,
        "type": "follow",
        "q": _cap_str(q_text, max_chars),
        "a": "",
        "feedback": "",
        "created_at": _now(),
        "meta": {"source": "generated", "main_idx": plan["main_idx"], "follow_idx": plan["follow_idx"]},
        "followups": [],
    }
    history.append(turn)
    return qid

# ---------------------------------
# 편의/관리 유틸 (선택적 사용)
# ---------------------------------
def get_turn(history: List[Dict[str, Any]], qid: str) -> Optional[Dict[str, Any]]:
    for h in (history or []):
        if str(h.get("id")) == str(qid):
            return h
    return None

def set_turn_field(history: List[Dict[str, Any]], qid: str, field: str, value: Any, plan: Optional[Dict[str, Any]] = None) -> bool:
    """
    q/a/feedback 같은 필드 업데이트. 길이 제한 적용.
    """
    h = get_turn(history, qid)
    if not h:
        return False
    if plan:
        max_chars = int(ensure_plan(plan).get("max_chars_per_field", 12000))
        if isinstance(value, str):
            value = _cap_str(value, max_chars)
    h[field] = value
    return True

def add_answer(history: List[Dict[str, Any]], qid: str, answer_text: str, plan: Optional[Dict[str, Any]] = None, append: bool = False) -> bool:
    h = get_turn(history, qid)
    if not h:
        return False
    plan = ensure_plan(plan or {})
    max_chars = int(plan.get("max_chars_per_field", 12000))
    if append and h.get("a"):
        new_val = (h.get("a", "") + "\n" + (answer_text or ""))
    else:
        new_val = (answer_text or "")
    h["a"] = _cap_str(new_val, max_chars)
    return True

def add_feedback(history: List[Dict[str, Any]], qid: str, feedback_text: str, plan: Optional[Dict[str, Any]] = None, append: bool = False) -> bool:
    h = get_turn(history, qid)
    if not h:
        return False
    plan = ensure_plan(plan or {})
    max_chars = int(plan.get("max_chars_per_field", 12000))
    if append and h.get("feedback"):
        new_val = (h.get("feedback", "") + "\n" + (feedback_text or ""))
    else:
        new_val = (feedback_text or "")
    h["feedback"] = _cap_str(new_val, max_chars)
    return True

def rebuild_ids(history: List[Dict[str, Any]]) -> None:
    """
    현재 순서대로 메인/팔로업 번호를 재부여.
    - 메인은 1..N
    - 팔로업은 해당 메인에 종속하여 1..k
    """
    main_idx = 0
    follow_idx = 0
    current_main_id = None
    for h in (history or []):
        t = h.get("type")
        if t == "main":
            main_idx += 1
            follow_idx = 0
            current_main_id = str(main_idx)
            h["id"] = current_main_id
            if "meta" in h and isinstance(h["meta"], dict):
                h["meta"]["main_idx"] = main_idx
                h["meta"]["follow_idx"] = 0
        elif t == "follow":
            if current_main_id is None:
                # 고립 팔로업 → 다음 메인에 묶일 수 없으므로 스킵/보정
                continue
            follow_idx += 1
            h["id"] = f"{current_main_id}-{follow_idx}"
            if "meta" in h and isinstance(h["meta"], dict):
                h["meta"]["main_idx"] = int(current_main_id)
                h["meta"]["follow_idx"] = follow_idx

def trim_history(history: List[Dict[str, Any]], max_turns: int) -> None:
    """
    초과분을 앞에서부터 제거(오래된 턴 삭제). 번호는 필요 시 rebuild_ids() 호출로 재정렬.
    """
    if max_turns <= 0:
        return
    n = len(history or [])
    if n <= max_turns:
        return
    # 오래된 것부터 drop
    drop = n - max_turns
    del history[:drop]

def compute_stats(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    mains = sum(1 for h in (history or []) if h.get("type") == "main")
    follows = sum(1 for h in (history or []) if h.get("type") == "follow")
    answered = sum(1 for h in (history or []) if (h.get("a") or "").strip())
    feedbacked = sum(1 for h in (history or []) if (h.get("feedback") or "").strip())
    return {
        "turns": len(history or []),
        "mains": mains,
        "follows": follows,
        "answered": answered,
        "feedbacked": feedbacked,
        "ratio_answered": (answered / max(1, (mains + follows))),
    }

def snapshot(history: List[Dict[str, Any]], plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    직렬화 가능한 스냅샷(파일 저장/전송 용)
    """
    return {
        "created_at": _now(),
        "plan": dict(plan or {}),
        "history": [dict(h) for h in (history or [])],
        "version": 1,
    }

def restore(snap: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    스냅샷 복구. 필수 키 없으면 안전 기본값 채움.
    """
    hist = list(snap.get("history", []) or [])
    plan = ensure_plan(snap.get("plan", {}) or {})
    return hist, plan

def current_main_id(plan: Dict[str, Any]) -> Optional[str]:
    idx = int(ensure_plan(plan).get("main_idx", 0))
    return str(idx) if idx > 0 else None

def can_add_follow(history: List[Dict[str, Any]], plan: Dict[str, Any]) -> bool:
    plan = ensure_plan(plan)
    if plan.get("main_idx", 0) <= 0:
        return False
    total_follow = sum(1 for h in (history or []) if h.get("type") == "follow")
    if total_follow >= int(plan.get("max_follow", 6)):
        return False
    per_main_limit = int(plan.get("follow_per_main", 2))
    cm = str(plan["main_idx"])
    per_main_count = sum(1 for h in (history or []) if h.get("type") == "follow" and str(h.get("id", "")).startswith(cm + "-"))
    return per_main_count < per_main_limit

def to_compact(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    경량 전송/렌더용: q/a 중심 최소 필드만 유지
    """
    out: List[Dict[str, Any]] = []
    for h in (history or []):
        out.append({
            "id": h.get("id", ""),
            "type": h.get("type", ""),
            "q": h.get("q", ""),
            "a": h.get("a", ""),
            "feedback": h.get("feedback", ""),
        })
    return out
