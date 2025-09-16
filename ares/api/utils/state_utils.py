# ares/api/utils/state_utils.py
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
    plan.setdefault("mode", "혼합형(추천)")
    plan.setdefault("difficulty", "보통")
    plan.setdefault("outline", [])
    plan.setdefault("cursor", 0)

    plan.setdefault("question_bank", [])
    plan.setdefault("bank_cursor", 0)

    plan.setdefault("main_idx", 0)
    plan.setdefault("follow_idx", 0)

    plan.setdefault("follow_per_main", 2)
    plan.setdefault("max_follow", 6)

    plan.setdefault("max_mains", 20)
    plan.setdefault("max_turns", 120)
    plan.setdefault("max_chars_per_field", 12000)
    return plan

def add_main_turn(history: List[Dict[str, Any]], plan: Dict[str, Any], q_text: str) -> str:
    plan = ensure_plan(plan)
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
    plan = ensure_plan(plan)
    if plan.get("main_idx", 0) <= 0:
        raise ValueError("먼저 메인 질문을 추가하세요.")

    total_follow = sum(1 for h in (history or []) if h.get("type") == "follow")
    if total_follow >= int(plan.get("max_follow", 6)):
        raise ValueError("팔로업 질문 최대치에 도달했습니다.")

    per_main_limit = int(plan.get("follow_per_main", 2))
    current_main_id = str(plan["main_idx"])
    per_main_count = sum(
        1 for h in (history or []) if h.get("type") == "follow" and str(h.get("id", "")).startswith(current_main_id + "-")
    )
    if per_main_count >= per_main_limit:
        raise ValueError(f"이 메인 질문({current_main_id})에 대한 팔로업은 최대 {per_main_limit}개입니다.")

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

def get_turn(history: List[Dict[str, Any]], qid: str) -> Optional[Dict[str, Any]]:
    for h in (history or []):
        if str(h.get("id")) == str(qid):
            return h
    return None

def set_turn_field(history: List[Dict[str, Any]], qid: str, field: str, value: Any, plan: Optional[Dict[str, Any]] = None) -> bool:
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
                continue
            follow_idx += 1
            h["id"] = f"{current_main_id}-{follow_idx}"
            if "meta" in h and isinstance(h["meta"], dict):
                h["meta"]["main_idx"] = int(current_main_id)
                h["meta"]["follow_idx"] = follow_idx

def trim_history(history: List[Dict[str, Any]], max_turns: int) -> None:
    if max_turns <= 0:
        return
    n = len(history or [])
    if n <= max_turns:
        return
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
    return {
        "created_at": _now(),
        "plan": dict(plan or {}),
        "history": [dict(h) for h in (history or [])],
        "version": 1,
    }

def restore(snap: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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

# =================================
# 직렬화 안전 변환기
# =================================
def to_jsonable(x):
    """
    dict/list 내부 비-JSON 타입을 안전 변환
    - datetime/date -> isoformat()
    - UUID -> str
    - bytes -> base64 str
    - set/tuple -> list
    - numpy -> python 기본형/리스트
    - dataclass/pydantic -> dict
    - 알 수 없는 타입 -> str() 최후 방어
    """
    from datetime import date
    from uuid import UUID
    import base64

    try:
        import numpy as np  # type: ignore
    except Exception:
        np = None

    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, UUID):
        return str(x)
    if isinstance(x, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(x)).decode("utf-8")
    if isinstance(x, (list, tuple, set)):
        return [to_jsonable(i) for i in x]
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}

    # dataclass
    try:
        from dataclasses import is_dataclass, asdict
        if is_dataclass(x):
            return to_jsonable(asdict(x))
    except Exception:
        pass

    # pydantic v2/v1
    if hasattr(x, "model_dump"):
        return to_jsonable(x.model_dump())
    if hasattr(x, "dict"):
        return to_jsonable(x.dict())

    # numpy
    if np is not None:
        if isinstance(x, np.generic):
            return x.item()
        if isinstance(x, np.ndarray):
            return x.tolist()

    return str(x)
