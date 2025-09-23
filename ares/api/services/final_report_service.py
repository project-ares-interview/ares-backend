from __future__ import annotations
from typing import Dict, Any

from django.utils import timezone

from ares.api.models import InterviewSession, InterviewTurn
from ares.api.models.interview_report import InterviewReport
from ares.api.services import resume_service
from ares.api.services.rag.final_interview_rag import RAGInterviewBot
from ares.api.utils.common_utils import get_logger

log = get_logger(__name__)


def _ensure_ncs_dict(ncs_ctx: Any) -> Dict[str, Any]:
    return ncs_ctx if isinstance(ncs_ctx, dict) else {}


def build_transcript_and_scores(session: InterviewSession) -> tuple[list[dict], list[dict]]:
    turns = session.turns.order_by("turn_index").all()
    transcript: list[dict] = []
    structured_scores: list[dict] = []

    interviewer_turns = {turn.turn_label: turn for turn in turns if turn.role == InterviewTurn.Role.INTERVIEWER}
    candidate_answers = {turn.turn_label: turn for turn in turns if turn.role == InterviewTurn.Role.CANDIDATE}

    for label, interviewer_turn in interviewer_turns.items():
        candidate_turn = candidate_answers.get(label)

        transcript.append({
            "role": "interviewer",
            "text": interviewer_turn.question,
            "id": label,
        })

        if candidate_turn:
            transcript.append({
                "role": "candidate",
                "text": candidate_turn.answer,
                "id": label,
            })
            if candidate_turn.scores:
                structured_scores.append(candidate_turn.scores)
            else:
                structured_scores.append({
                    "question_id": label,
                    "question": interviewer_turn.question,
                    "answer": candidate_turn.answer,
                    "scoring": {"scoring_reason": "평가 제외 항목입니다."}
                })
        else:
            transcript.append({
                "role": "candidate",
                "text": "[답변 없음]",
                "id": label,
            })
            structured_scores.append({
                "question_id": label,
                "question": interviewer_turn.question,
                "answer": "[답변 없음]",
                "scoring": {"scoring_reason": "평가 불가 (답변 없음)"}
            })

    return transcript, structured_scores


def analyze_resume(session: InterviewSession, rag_context: dict, jd_context: str, resume_context: str, research_context: str) -> dict:
    try:
        company_meta = {
            "company_name": rag_context.get("company_name", ""),
            "job_title": rag_context.get("job_title", ""),
        }
        return resume_service.analyze_all(
            jd_text=jd_context,
            resume_text=resume_context,
            research_text=research_context,
            company_meta=company_meta,
        )
    except Exception:
        log.exception("[%s] 이력서 분석 실패", session.id)
        return {"error": "Resume analysis failed"}


def generate_final_report(session: InterviewSession) -> dict:
    rag_context = session.rag_context or {}
    if not rag_context.get("company_name") or not rag_context.get("job_title"):
        raise ValueError("RAG 컨텍스트에 회사/직무 정보가 없습니다.")

    jd_context = getattr(getattr(session.user, "profile", None), "jd_context", "") or (session.jd_context or "")
    resume_context = getattr(getattr(session.user, "profile", None), "resume_context", "") or (session.resume_context or "")
    research_context = getattr(getattr(session.user, "profile", None), "research_context", "")

    rag_bot = RAGInterviewBot(
        company_name=rag_context.get("company_name", ""),
        job_title=rag_context.get("job_title", ""),
        interviewer_mode=session.interviewer_mode,
        resume_context=resume_context,
        ncs_context=_ensure_ncs_dict(session.context),
        jd_context=jd_context,
    )

    transcript, structured_scores = build_transcript_and_scores(session)
    full_resume_analysis = analyze_resume(session, rag_context, jd_context, resume_context, research_context)
    interview_plan = (rag_context.get("interview_plans", {}) or {}).get("raw_v2_plan", {})

    final_report = rag_bot.build_final_report(
        transcript=transcript,
        structured_scores=structured_scores,
        interview_plan=interview_plan,
        full_resume_analysis=full_resume_analysis,
        full_contexts={
            "jd_context": jd_context,
            "resume_context": resume_context,
            "research_context": research_context,
        },
    )
    return final_report


def upsert_interview_report(session: InterviewSession, final_report: dict) -> InterviewReport:
    values = {
        "overall_summary": final_report.get("overall_summary", ""),
        "interview_flow_rationale": final_report.get("interview_flow_rationale", ""),
        "strengths_matrix": final_report.get("strengths_matrix", []),
        "weaknesses_matrix": final_report.get("weaknesses_matrix", []),
        "score_aggregation": final_report.get("score_aggregation", {}),
        "missed_opportunities": final_report.get("missed_opportunities", []),
        "potential_followups_global": final_report.get("potential_followups_global", []),
        "resume_feedback": final_report.get("resume_feedback", {}),
        "hiring_recommendation": final_report.get("hiring_recommendation", ""),
        "next_actions": final_report.get("next_actions", []),
        "question_by_question_feedback": final_report.get("question_by_question_feedback", []),
        "tags": final_report.get("tags", []),
        "version": str(final_report.get("version", "")),
    }
    report, created = InterviewReport.objects.update_or_create(
        user=session.user,
        session=session,
        defaults=values,
    )
    log.info("[%s] InterviewReport %s: %s", session.id, "created" if created else "updated", report.id)
    return report


def finalize_interview_session(session: InterviewSession) -> dict:
    final_report = generate_final_report(session)
    # update session status/meta
    session.status = InterviewSession.Status.FINISHED
    session.finished_at = timezone.now()
    session.meta = {**(session.meta or {}), "final_report": final_report}
    session.save(update_fields=["status", "finished_at", "meta"])
    upsert_interview_report(session, final_report)
    return final_report
