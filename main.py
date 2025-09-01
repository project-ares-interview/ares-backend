# main.py — 완전 모듈화 버전 (dotenvx 실행 전제) [STT 텍스트 표시 반영]
import os, time
from typing import List, Dict, Any, Optional
import gradio as gr

# 공용/입수
from ares.api.utils.common_utils import ts as _ts, ensure_dir as _ensure_dir, LOG_ROOT
from ares.api.utils.file_utils import (
    collect_context, virtual_append, join_texts, auto_split_resume_cover
)
from ares.api.utils.state_utils import (
    history_labels, ensure_plan, add_main_turn, add_follow_turn
)

# AI/면접/문서분석/음성
from ares.api.services.interview_service import (
    make_outline, generate_main_question_ondemand, question_for_section,
    generate_followups, analyze_answer_star_c, generate_company_specific_questions
)
from ares.api.services.resume_service import (
    analyze_resume_or_cover, compare_documents, analyze_research_alignment
)
from ares.api.services.speech_service import stt_from_file, tts_play


# -------- Handlers (UI 로직) --------
def on_ingest_inputs(jd_files, jd_paste, doc_files, doc_paste, research_files, research_paste):
    progress = gr.Progress(track_tqdm=True)
    progress(0.02, desc="자료 파싱 시작")

    # JD
    progress(0.15, desc="JD 텍스트 추출")
    jd_ctx, jd_map = collect_context(jd_files)
    if jd_paste and jd_paste.strip():
        virtual_append(jd_map, "JD(붙여넣기).txt", jd_paste)
        jd_ctx = join_texts(jd_ctx, f"# [JD(붙여넣기)]\n{jd_paste}")

    # 지원서
    progress(0.45, desc="지원서 텍스트 추출")
    doc_ctx, doc_map = collect_context(doc_files)
    if doc_paste and doc_paste.strip():
        virtual_append(doc_map, "지원서(붙여넣기).txt", doc_paste)
        doc_ctx = join_texts(doc_ctx, f"# [지원서(붙여넣기)]\n{doc_paste}")

    # 자동 분할 (붙여넣기 문서는 제외 + 최소 2개 상이한 결과일 때만 반영)
    progress(0.6, desc="지원서 자동 분할")
    exp = dict(doc_map)
    for name, text in list(doc_map.items()):
        if "붙여넣기" in name:
            continue
        v = auto_split_resume_cover(name, text)
        if v and len(v) >= 2 and any(k != name for k in v.keys()):
            exp.update(v)
    doc_map = exp

    # 리서치
    progress(0.8, desc="리서치 텍스트 추출")
    research_ctx, research_map = collect_context(research_files)
    if research_paste and research_paste.strip():
        virtual_append(research_map, "리서치(붙여넣기).txt", research_paste)
        research_ctx = join_texts(research_ctx, f"# [리서치(붙여넣기)]\n{research_paste}")

    progress(1.0, desc="파싱 완료")

    names = sorted(list(doc_map.keys()))
    status_msg = (
        f"✅ 파싱 완료\n"
        f"- JD 문서: {len(jd_map)}개 / 지원서 문서: {len(doc_map)}개 / 리서치 문서: {len(research_map)}개\n"
        f"- 가상문서 자동 생성: {'있음' if any('#' in n for n in names) else '없음'}"
    )
    return (
        jd_ctx, jd_map, doc_ctx, doc_map, research_ctx, research_map,
        status_msg,
        gr.update(choices=names), gr.update(choices=names, value=[])
    )


def on_run_all_analyses(doc_map: Dict[str,str], jd_ctx: str, research_ctx: str, doc_multi: List[str]):
    progress = gr.Progress()
    progress(0.02, desc="분석 준비")
    names_all = [n for n, v in doc_map.items() if (v or "").strip()]
    virtual_pref = [n for n in names_all if ("#이력서" in n or "#자소서" in n)]
    targets = doc_multi if doc_multi else (virtual_pref if len(virtual_pref) >= 1 else names_all[:3])

    # 1) 심층 분석
    progress(0.18, desc="심층 분석")
    deep_results = []
    if targets:
        total = len(targets)
        for i, name in enumerate(targets, start=1):
            progress(0.18 + 0.35*(i/total), desc=f"심층 분석… ({i}/{total})")
            txt = doc_map.get(name, "")
            if txt.strip():
                deep = analyze_resume_or_cover(txt, jd_text=jd_ctx)
                deep_results.append(f"## [{name}] 심층 분석\n{deep}\n")
    deep_out = "\n\n".join(deep_results) if deep_results else "분석 가능한 지원서 텍스트가 없습니다."

    # 2) 교차 분석
    progress(0.58, desc="교차 분석")
    cmp_out = "교차 분석은 최소 2개 문서가 필요합니다."
    if len(targets) >= 2:
        named = {n: doc_map.get(n, "") for n in targets}
        named = {k:v for k,v in named.items() if v.strip()}
        if len(named) >= 2:
            cmp_out = compare_documents(named)

    # 3) 정합성
    progress(0.82, desc="정합성 점검")
    doc_concat = "\n\n".join([f"[{n}]\n{doc_map[n]}" for n in targets if (doc_map.get(n,"").strip())])[:16000]
    aln_out = "JD/지원서/리서치 세 가지가 모두 필요합니다."
    if (jd_ctx or "").strip() and doc_concat.strip() and (research_ctx or "").strip():
        aln_out = analyze_research_alignment(jd_ctx, doc_concat, research_ctx)

    progress(1.0, desc="완료")
    results = {"심층 분석": deep_out, "교차 분석": cmp_out, "정합성 점검": aln_out}
    choices = [k for k,v in results.items() if (v or "").strip()]
    default_key = choices[0] if choices else "심층 분석"
    return results, gr.update(choices=choices, value=default_key), results.get(default_key, "결과가 없습니다.")


def on_select_analysis_view(results: Dict[str, str], selected_key: str):
    """분석 결과 선택 변경 시 해당 본문 반환"""
    if not results:
        return "결과가 없습니다."
    if not selected_key:
        for v in results.values():
            if (v or "").strip():
                return v
        return "결과가 없습니다."
    return results.get(selected_key, "결과가 없습니다.")


def _use_research_ctx(research_bias: bool, research_ctx: str) -> bool:
    return bool(research_bias and (research_ctx or "").strip())


def on_start_interview(mode, outline_k, difficulty, use_tts, voice, research_bias,
                       history, plan, jd_ctx_state, doc_ctx_state, research_ctx_state):
    progress = gr.Progress()
    progress(0.1, desc="면접 컨텍스트 구성")

    plan = ensure_plan(plan)
    plan["mode"] = mode
    plan["difficulty"] = difficulty

    base_context = join_texts("## [공고/JD]\n"+(jd_ctx_state or ""), "## [지원서]\n"+(doc_ctx_state or ""), limit=22000)
    full_context = join_texts(base_context, "## [지원자 리서치]\n"+(research_ctx_state or ""), limit=24000)
    use_research = _use_research_ctx(research_bias, research_ctx_state)
    ctx = full_context if use_research else base_context

    progress(0.6, desc="첫 질문 생성")
    prev_qs = [h["q"] for h in (history or [])]

    if mode == "프리플랜":
        if not plan.get("question_bank"):
            seed = (generate_company_specific_questions(ctx, (research_ctx_state or ""), [], difficulty)
                    if use_research else generate_main_question_ondemand(ctx, [], difficulty))
            plan["question_bank"] = [seed] if isinstance(seed, str) else (seed or [])
            plan["bank_cursor"] = 0
        if plan["bank_cursor"] >= len(plan["question_bank"]):
            return ("준비된 질문이 끝났습니다.", "", gr.update(choices=[], value=None),
                    history, plan, None, gr.update(choices=history_labels(history), value=(history[-1]["id"] if history else None) if history else None))
        q_text = plan["question_bank"][plan["bank_cursor"]]
        plan["bank_cursor"] += 1

    elif mode == "혼합형(추천)":
        if not plan.get("outline"):
            plan["outline"] = make_outline(ctx, n=int(outline_k))
            plan["cursor"] = 0
        if plan["cursor"] >= len(plan["outline"]):
            return ("준비된 섹션이 끝났습니다.", "", gr.update(choices=[], value=None),
                    history, plan, None, gr.update(choices=history_labels(history), value=(history[-1]["id"] if history else None) if history else None))
        section = plan["outline"][plan["cursor"]]
        q_text = question_for_section(ctx, section, prev_qs, difficulty)
        plan["cursor"] += 1  # 생성 후 증가

    else:  # 온디맨드
        q_text = (generate_company_specific_questions(ctx, (research_ctx_state or ""), prev_qs, difficulty)
                  if use_research else generate_main_question_ondemand(ctx, prev_qs, difficulty))
        if isinstance(q_text, list):
            q_text = q_text[0] if q_text else "적절한 질문을 생성하지 못했습니다."

    qid = add_main_turn(history, plan, q_text)
    tts_path = tts_play(q_text, voice) if use_tts else None

    progress(1.0, desc="완료")
    return (f"{qid}. {q_text}", "", gr.update(choices=[], value=None),
            history, plan, tts_path, gr.update(choices=history_labels(history), value=qid))


def on_answer(ans_text, ans_audio, followup_mode, speak_fb, voice, history, plan):
    """
    - 음성 파일(ans_audio)이 있으면 STT를 우선 적용하여 인식 텍스트를 answer_box에도 표시.
    - 모든 return 튜플 마지막 항목으로 'answer_box에 넣을 텍스트'를 추가하여 UI를 갱신.
    """
    if not history:
        return (
            "먼저 '첫 질문 생성'을 눌러 면접을 시작하세요.",
            "", gr.update(choices=[], value=None), history, plan, None,
            gr.update(choices=[], value=None),
            ans_text  # answer_box 그대로 유지
        )

    # 우선 텍스트 박스 값
    a = (ans_text or "").strip()

    # 음성이 있으면 STT를 우선 적용
    stt_text = ""
    if ans_audio:
        stt_text = stt_from_file(ans_audio) or ""
        if stt_text.strip():
            a = stt_text.strip()

    if not a:
        return (
            "답변이 비어 있습니다.", "", gr.update(choices=[], value=None),
            history, plan, None,
            gr.update(choices=history_labels(history), value=(history[-1]["id"] if history else None)),
            stt_text or ans_text  # STT 실패면 기존 텍스트 유지
        )

    # 기록/분석
    cur = history[-1]
    cur["a"] = a

    fb = analyze_answer_star_c(cur["q"], a)
    fus = generate_followups(cur["q"], a, mode=followup_mode)
    cur["feedback"] = fb
    cur["followups"] = fus

    tts_path = tts_play(fb, voice) if speak_fb else None

    return (
        fb, "\n".join(fus),
        gr.update(choices=fus, value=(fus[0] if fus else None)),
        history, plan, tts_path,
        gr.update(choices=history_labels(history), value=cur["id"]),
        a  # answer_box에 표시할 최종 텍스트(STT 결과 또는 입력 텍스트)
    )


def on_next_followup(selected_followup, use_tts, voice, history, plan):
    if not history:
        return "", "", gr.update(choices=[], value=None), history, plan, None, gr.update(choices=[], value=None)
    q = (selected_followup or "").strip()
    if not q:
        last = history[-1].get("followups", [])
        if not last:
            return "더 이상 이어갈 꼬리질문이 없습니다.", "", gr.update(choices=[], value=None), history, plan, None, gr.update(choices=history_labels(history), value=history[-1]["id"])
        q = last[0]
    qid = add_follow_turn(history, plan, q)
    tts_path = tts_play(q, voice) if use_tts else None
    return (f"{qid}. {q}", "", gr.update(choices=[], value=None),
            history, plan, tts_path,
            gr.update(choices=history_labels(history), value=qid))


def on_next_main(jd_ctx, doc_ctx, research_ctx, research_bias, use_tts, voice, history, plan):
    plan = ensure_plan(plan)
    mode = plan.get("mode","온디맨드")
    difficulty = plan.get("difficulty","보통")

    base_context = join_texts("## [공고/JD]\n"+(jd_ctx or ""), "## [지원서]\n"+(doc_ctx or ""), limit=22000)
    full_context = join_texts(base_context, "## [지원자 리서치]\n"+(research_ctx or ""), limit=24000)
    use_research = _use_research_ctx(research_bias, research_ctx)
    ctx = full_context if use_research else base_context
    prev_qs = [h["q"] for h in (history or [])]

    if mode == "프리플랜":
        if plan.get("bank_cursor", 0) >= len(plan.get("question_bank", [])):
            return ("준비된 질문이 끝났습니다.", "", gr.update(choices=[], value=None),
                    history, plan, None, gr.update(choices=history_labels(history), value=(history[-1]["id"] if history else None) if history else None))
        q_text = plan["question_bank"][plan["bank_cursor"]]
        plan["bank_cursor"] += 1

    elif mode == "혼합형(추천)":
        if not plan.get("outline"):
            plan["outline"] = make_outline(ctx, n=5)
            plan["cursor"] = 0
        if plan["cursor"] >= len(plan["outline"]):
            return ("준비된 섹션이 끝났습니다.", "", gr.update(choices=[], value=None),
                    history, plan, None, gr.update(choices=history_labels(history), value=(history[-1]["id"] if history else None) if history else None))
        section = plan["outline"][plan["cursor"]]
        q_text = question_for_section(ctx, section, prev_qs, difficulty)
        plan["cursor"] += 1

    else:  # 온디맨드
        q_text = (generate_company_specific_questions(ctx, (research_ctx or ""), prev_qs, difficulty)
                  if use_research else generate_main_question_ondemand(ctx, prev_qs, difficulty))
        if isinstance(q_text, list):
            q_text = q_text[0] if q_text else "적절한 질문을 생성하지 못했습니다."

    qid = add_main_turn(history, plan, q_text)
    tts_path = tts_play(q_text, voice) if use_tts else None
    return (f"{qid}. {q_text}", "", gr.update(choices=[], value=None),
            history, plan, tts_path,
            gr.update(choices=history_labels(history), value=qid))


def on_select_history(sel_id, history):
    if not sel_id or not history:
        return "", "", "", ""
    idx = next((i for i,h in enumerate(history) if h["id"]==sel_id), None)
    if idx is None: return "", "", "", ""
    t = history[idx]
    fus = "\n".join([f"- {x}" for x in t.get("followups", [])])
    view_q = f"{t['id']}. {t.get('q','')}"
    return view_q, t.get("a",""), t.get("feedback",""), fus


def on_finish(history, analysis_results=None):
    if not history and not analysis_results:
        return "기록이 없습니다. 먼저 문서 분석/면접을 진행해 주세요.", ""

    lines = [f"# 최종 리포트\n- 생성 시각: {_ts()}\n"]

    if analysis_results:
        lines.append("\n## 🧠 문서 분석 결과\n")
        for key in ["심층 분석", "교차 분석", "정합성 점검"]:
            val = (analysis_results or {}).get(key, "")
            if val and val.strip():
                lines.append(f"### {key}\n{val}\n")

    if history:
        lines.append(f"\n## 🎤 면접 기록 (총 {len(history)}턴)\n")
        for t in history:
            lines.append(f"### {t['id']}  {'(메인)' if t['type']=='main' else '(꼬리)'}\n{t['q']}\n")
            lines.append(f"- **답변**\n{t['a']}\n")
            lines.append(f"- **피드백(STAR+C)**\n{t['feedback']}\n")
            if t.get("followups"):
                lines.append("  - **해당 턴의 꼬리질문 후보**\n" + "\n".join([f"    - {x}" for x in t['followups']]) + "\n")
            lines.append("---\n")

    content = "\n".join(lines)
    _ensure_dir(LOG_ROOT)
    path = os.path.join(LOG_ROOT, f"report_{int(time.time())}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"✅ 최종 리포트를 생성했습니다.\n경로: `{path}`", content


# -------- Gradio UI --------
with gr.Blocks(theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🤖 한큐 준비: 문서 점검 → 면접 연습 → 최종 리포트")
    history_state   = gr.State(value=[])
    plan_state      = gr.State(value={})
    jd_ctx_state    = gr.State(value=""); jd_filemap_state = gr.State(value={})
    doc_ctx_state   = gr.State(value=""); doc_filemap_state = gr.State(value={})
    research_ctx_state = gr.State(value=""); research_filemap_state = gr.State(value={})
    analysis_results_state = gr.State(value={"심층 분석":"", "교차 분석":"", "정합성 점검":""})

    with gr.Tabs():
        with gr.Tab("1) 문서 점검"):
            with gr.Row():
                with gr.Column(scale=2):
                    jd_files = gr.File(label="공고/JD 업로드", file_count="multiple", type="filepath")
                    jd_paste = gr.Textbox(label="(선택) JD 붙여넣기", lines=5)
                    doc_files = gr.File(label="지원서 업로드", file_count="multiple", type="filepath")
                    doc_paste = gr.Textbox(label="(선택) 지원서 붙여넣기", lines=5)
                    research_files = gr.File(label="리서치 업로드", file_count="multiple", type="filepath")
                    research_paste = gr.Textbox(label="(선택) 리서치 붙여넣기", lines=5)
                    ingest_btn = gr.Button("① 자료 불러오기 / 파싱", variant="primary")
                with gr.Column(scale=1):
                    status_md = gr.Markdown("파싱 상태가 여기에 표시됩니다.")
            gr.Markdown("---")
            gr.Markdown("### 문서 분석 — 일괄 실행 & 뷰")
            doc_multi = gr.Dropdown(choices=[], value=[], multiselect=True, label="(선택) 대상 문서")
            run_all_btn = gr.Button("② 문서 분석 실행", variant="primary")
            analysis_view = gr.Dropdown(choices=[], value=None, label="분석 결과 보기")
            analysis_md = gr.Markdown(label="결과 본문")

        with gr.Tab("2) 면접 연습"):
            with gr.Row():
                with gr.Column(scale=1):
                    mode_dd = gr.Dropdown(choices=["온디맨드","프리플랜","혼합형(추천)"], value="혼합형(추천)", label="질문 모드")
                    outline_k = gr.Slider(3, 8, value=5, step=1, label="섹션/문항 수(혼합형)")
                    difficulty_dd = gr.Dropdown(choices=["쉬움","보통","어려움"], value="보통", label="난이도")
                    followup_mode = gr.Radio(choices=["evidence","why","how","risk"], value="evidence", label="꼬리질문 방향")
                    use_tts = gr.Checkbox(label="질문 TTS", value=False)
                    speak_feedback = gr.Checkbox(label="피드백 TTS", value=False)
                    tts_voice = gr.Dropdown(choices=["ko-KR-HyunsuNeural","ko-KR-SunHiNeural","ko-KR-InJoonNeural"], value="ko-KR-HyunsuNeural", label="TTS 음성")
                    research_bias = gr.Checkbox(label="리서치 반영", value=True)
                    start_btn = gr.Button("첫 질문 생성 ▶", variant="primary")
                with gr.Column(scale=2):
                    question_box = gr.Textbox(label="현재 질문(번호 자동)", interactive=False, lines=3)
                    answer_box   = gr.Textbox(label="나의 답변 (텍스트)", lines=5)
                    answer_audio = gr.Audio(sources=["microphone","upload"], type="filepath", label="또는 음성으로")
                    ans_btn      = gr.Button("답변 제출 → STARC + 꼬리질문", variant="primary")
            with gr.Row():
                with gr.Column(scale=2):
                    feedback_md  = gr.Markdown(label="STARC 피드백")
                    followups_md = gr.Textbox(label="꼬리질문(목록)", interactive=False, lines=3)
                    followup_sel = gr.Radio(choices=[], label="이어갈 꼬리질문 선택", interactive=True)
                    next_fu_btn   = gr.Button("선택 꼬리질문으로 진행")
                with gr.Column(scale=1):
                    tts_q  = gr.Audio(label="질문 음성", interactive=False)
                    tts_fb = gr.Audio(label="피드백 음성", interactive=False)
                    next_main_btn = gr.Button("새 메인 질문 진행")

            gr.Markdown("---")
            history_dd = gr.Dropdown(choices=[], value=None, label="턴 선택")
            view_q = gr.Textbox(label="질문", interactive=False)
            view_a = gr.Textbox(label="답변", interactive=False)
            view_fb = gr.Textbox(label="피드백", interactive=False)
            view_fus = gr.Textbox(label="꼬리질문 후보", interactive=False)

        with gr.Tab("3) 최종 리포트"):
            finish_btn = gr.Button("리포트 생성", variant="primary")
            finish_out_msg = gr.Markdown(label="리포트 생성 결과")
            finish_out_md  = gr.Markdown(label="리포트 미리보기")

    # Bindings
    ingest_btn.click(
        fn=on_ingest_inputs,
        inputs=[jd_files, jd_paste, doc_files, doc_paste, research_files, research_paste],
        outputs=[jd_ctx_state, jd_filemap_state, doc_ctx_state, doc_filemap_state, research_ctx_state, research_filemap_state,
                 status_md, doc_multi, doc_multi]
    )
    run_all_btn.click(
        fn=on_run_all_analyses,
        inputs=[doc_filemap_state, jd_ctx_state, research_ctx_state, doc_multi],
        outputs=[analysis_results_state, analysis_view, analysis_md]
    )
    analysis_view.change(
        fn=on_select_analysis_view,
        inputs=[analysis_results_state, analysis_view],
        outputs=[analysis_md]
    )
    start_btn.click(
        fn=on_start_interview,
        inputs=[mode_dd, outline_k, difficulty_dd, use_tts, tts_voice, research_bias,
                history_state, plan_state, jd_ctx_state, doc_ctx_state, research_ctx_state],
        outputs=[question_box, answer_box, followup_sel, history_state, plan_state, tts_q, history_dd]
    )
    ans_btn.click(
        fn=on_answer,
        inputs=[answer_box, answer_audio, followup_mode, speak_feedback, tts_voice, history_state, plan_state],
        # ✅ 마지막에 answer_box를 추가로 갱신(총 8개 출력)
        outputs=[feedback_md, followups_md, followup_sel, history_state, plan_state, tts_fb, history_dd, answer_box]
    )
    next_fu_btn.click(
        fn=on_next_followup,
        inputs=[followup_sel, use_tts, tts_voice, history_state, plan_state],
        outputs=[question_box, answer_box, followup_sel, history_state, plan_state, tts_q, history_dd]
    )
    # research_bias를 그대로 전달
    next_main_btn.click(
        fn=on_next_main,
        inputs=[jd_ctx_state, doc_ctx_state, research_ctx_state, research_bias, use_tts, tts_voice, history_state, plan_state],
        outputs=[question_box, answer_box, followup_sel, history_state, plan_state, tts_q, history_dd]
    )
    history_dd.change(
        fn=on_select_history,
        inputs=[history_dd, history_state],
        outputs=[view_q, view_a, view_fb, view_fus]
    )
    finish_btn.click(
        fn=on_finish,
        inputs=[history_state, analysis_results_state],
        outputs=[finish_out_msg, finish_out_md]
    )

if __name__ == "__main__":
    demo.launch()
