# utils/state_utils.py
from __future__ import annotations
from typing import List, Dict, Any, Optional

def history_labels(history: List[Dict[str, Any]]) -> List[str]:
    return [h.get("id","") for h in (history or [])]

def ensure_plan(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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
    return plan

def add_main_turn(history: List[Dict[str, Any]], plan: Dict[str, Any], q_text: str) -> str:
    plan["main_idx"] += 1
    plan["follow_idx"] = 0
    qid = f"{plan['main_idx']}"
    history.append({"id": qid, "type": "main", "q": q_text, "a": "", "feedback": "", "followups": []})
    return qid

def add_follow_turn(history: List[Dict[str, Any]], plan: Dict[str, Any], q_text: str) -> str:
    plan["follow_idx"] += 1
    qid = f"{plan['main_idx']}-{plan['follow_idx']}"
    history.append({"id": qid, "type": "follow", "q": q_text, "a": "", "feedback": "", "followups": []})
    return qid
