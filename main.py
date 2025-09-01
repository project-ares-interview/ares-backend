# main.py — Ares 데모 앱 (면접 UX 개선 + 리포트 버튼 이동)
import os
import time
import gradio as gr
from typing import List, Dict, Any, Tuple

# --- 내부 모듈 임포트 (패키지 경로) ---
# 프로젝트 루트에서 실행을 가정. 필요시 sys.path 조정.
from ares.api.utils.common_utils import get_logger, save_json, load_json
from ares.api.utils.state_utils import ensure_plan, add_main_turn, add_follow_turn
from ares.api.utils.file_utils import join_texts
from ares.api.services.interview_service import (
    make_outline,
    generate_main_question_ondemand,
    question_for_section,
    generate_followups,
    analyze_answer_star_c,
)
# (서류 분석/정합도 필요시 사용 가능)
# from ares.api.services.resume_service import analyze_resume_or_cover, compare_documents, analyze_research_alignment

log = get_logger("ares-app")

# =========================
# 상태 구조
# =========================
# plan: {
#   mode, difficulty, outline, cursor, question_bank, bank_cursor,
#   main_idx, follow_idx, follow_per_main, max_follow, max_main
# }
# history: [ {id, type('main'|'follow'), q, a, feedback } ... ]

# ---- 유틸: 히스토리 HTML 렌더링 ----
def render_history_html(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "<div style='opacity:.6'>아직 기록이 없습니다.</div>"
    # 최신순 위에 보이도록 역순 렌더
    blocks = []
    for turn in reversed(history):
        qid = turn.get("id","")
        q = (turn.get("q") or "").strip()
        a = (turn.get("a") or "").strip()
        fb = (turn.get("feedback") or "").strip()
        typ = "메인" if turn.get("type") == "main" else "꼬리"
        card = f"""
        <div style="border:1px solid #e5e7eb;border-radius:14px;padding:12px;margin-bottom:10px;background:#0b0b0b0a">
          <div style="font-weight:600">[{qid}] ({typ}) {q}</div>
          {'<div style="margin-top:6px;white-space:pre-wrap">'+a+'</div>' if a else ''}
          {'<div style="margin-top:10px;padding:10px;border-radius:10px;background:#f8fafc;white-space:pre-wrap"><b>피드백(STAR-C)</b>\n'+fb+'</div>' if fb else ''}
        </div>
        """
        blocks.append(card)
    return "<div>"+ "\n".join(blocks) +"</div>"

# ---- 현재 질문(상단 고정)에 띄울 텍스트 생성 ----
def current_question_text(history: List[Dict[str,Any]]) -> str:
    if not history:
        return "**현재 질문이 없습니다. ‘면접 시작’으로 질문을 생성하세요.**"
    t = history[-1]
    return f"**[{t.get('id','')}] {t.get('q','')}**"

# ---- 자동 스크롤/탭 이동용 JS 주입 ----
def script_scroll_top() -> str:
    return "<script>window.scrollTo({top:0,behavior:'smooth'});</script>"

def script_focus_answer() -> str:
    # gradio에서 직접 autofocus 제어가 제한적이라 시도형 처리
    return "<script>const ta=[...document.querySelectorAll('textarea')].pop(); if(ta){ta.focus();}</script>"

def script_go_report_tab() -> str:
    # 탭 버튼 중 ‘리포트’ 텍스트 포함 요소를 찾아 클릭(버튼 구조 변경시 텍스트만 맞추면 됨)
    return ("<script>"
            "const btn=[...document.querySelectorAll('button')].find(b=>/리포트/.test(b.innerText));"
            "if(btn){btn.click();}"
            "window.scrollTo({top:0,behavior:'smooth'});"
            "</script>")

# =========================
# 콜백 로직
# =========================
def start_interview(context_text: str, difficulty: str, max_main: int,
                    plan: Dict[str,Any], history: List[Dict[str,Any]]):
    plan = ensure_plan(plan)
    plan["difficulty"] = difficulty or "보통"
    plan.setdefault("max_main", max_main)
    plan["max_main"] = max_main
    # 첫 질문 생성
    if plan["main_idx"] == 0:
        q = generate_main_question_ondemand(context_text or "", [h["q"] for h in history], plan["difficulty"])
        add_main_turn(history, plan, q)
    # 출력 업데이트
    cur_q_md = current_question_text(history)
    hist_html = render_history_html(history)
    # UX 스크립트
    ux = script_scroll_top() + script_focus_answer()
    return plan, history, cur_q_md, hist_html, gr.update(visible=True, value=ux)

def score_answer(answer_text: str, plan: Dict[str,Any], history: List[Dict[str,Any]]):
    if not history:
        return history, render_history_html(history), gr.update(visible=True, value="")
    # 최신 턴에 답변/피드백 채움
    history[-1]["a"] = (answer_text or "").strip()
    if history[-1]["a"]:
        fb = analyze_answer_star_c(history[-1]["q"], history[-1]["a"])
        history[-1]["feedback"] = fb
    hist_html = render_history_html(history)
    ux = script_scroll_top()
    return history, hist_html, gr.update(visible=True, value=ux)

def make_followup(context_text: str, plan: Dict[str,Any], history: List[Dict[str,Any]]):
    plan = ensure_plan(plan)
    # 현재 메인에 대한 꼬리 개수 제한
    if plan["follow_idx"] >= plan.get("follow_per_main", 2):
        gr.Warning("이번 메인 질문의 꼬리질문 최대 개수에 도달했습니다.")
        return plan, history, current_question_text(history), render_history_html(history), gr.update(visible=True, value=script_scroll_top())
    # 직전 턴(답변 기준)으로 꼬리질문 생성
    base = history[-1]
    q = base["q"]
    a = base.get("a","")
    fl = generate_followups(q, a or "", mode="evidence")
    follow_q = fl[0] if fl else "방금 답변에서 수치/지표를 더 구체화해 설명해 주시겠습니까?"
    add_follow_turn(history, plan, follow_q)
    # 화면 업데이트
    cur_q_md = current_question_text(history)
    hist_html = render_history_html(history)
    ux = script_scroll_top() + script_focus_answer()
    return plan, history, cur_q_md, hist_html, gr.update(visible=True, value=ux)

def next_main(context_text: str, plan: Dict[str,Any], history: List[Dict[str,Any]]):
    plan = ensure_plan(plan)
    # 개수 가드
    if plan["main_idx"] >= plan.get("max_main", 5):
        gr.Info("설정한 메인 질문 개수에 도달했습니다.")
        return plan, history, current_question_text(history), render_history_html(history), gr.update(visible=True, value=script_scroll_top())

    q = generate_main_question_ondemand(context_text or "", [h["q"] for h in history], plan["difficulty"])
    add_main_turn(history, plan, q)
    # 화면 업데이트
    cur_q_md = current_question_text(history)
    hist_html = render_history_html(history)
    ux = script_scroll_top() + script_focus_answer()
    return plan, history, cur_q_md, hist_html, gr.update(visible=True, value=ux)

# ---- 리포트 생성 ----
def build_report(history: List[Dict[str,Any]], extra_note: str = "") -> Tuple[str, str]:
    """히스토리 기반 간단 리포트(Markdown) 생성 후 저장. (path, markdown) 반환"""
    lines = ["# Ares 면접 세션 리포트", ""]
    if extra_note:
        lines += ["> 메모: " + extra_note, ""]
    for t in history:
        lines += [f"## Q{t['id']} — {'메인' if t['type']=='main' else '꼬리'}", f"**질문**: {t['q']}"]
        if t.get("a"):  lines += ["**답변**:\n"+t['a']]
        if t.get("feedback"): lines += ["**피드백(STAR-C)**:\n"+t['feedback']]
        lines.append("")
    md = "\n".join(lines).strip()
    out_dir = os.path.join(os.getcwd(), "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"ares_report_{int(time.time())}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path, md

def make_report_and_go(history: List[Dict[str,Any]], report_note: str):
    path, md = build_report(history, report_note or "")
    # 파일 경로와 미리보기, 그리고 탭 이동 스크립트 반환
    return path, md, gr.update(visible=True, value=script_go_report_tab())

# =========================
# UI
# =========================
def build_app():
    with gr.Blocks(css="""
    .sticky-top { position: sticky; top: 0; z-index: 10; background: var(--block-background-fill); }
    """, title="Ares — 차세대 AI 면접 솔루션") as demo:

        # --- 공유 상태 ---
        st_plan    = gr.State({})
        st_history = gr.State([])

        with gr.Tabs():
            # ----------------- 탭 1: 문서/컨텍스트 -----------------
            with gr.Tab("1) 컨텍스트/준비"):
                gr.Markdown("### 면접 컨텍스트 입력\nJD/경험 요약/회사 리서치 중 핵심을 붙여넣어 주세요.")
                context = gr.Textbox(label="컨텍스트", lines=10, placeholder="예) 설비 유지보수 JD 요약, 내 프로젝트 성과 요약, 회사 리서치 핵심 등")
                difficulty = gr.Dropdown(["쉬움","보통","어려움"], value="보통", label="난이도")
                max_main = gr.Slider(1, 10, value=5, step=1, label="메인 질문 개수")
                btn_start = gr.Button("면접 시작 (첫 질문 생성)")

            # ----------------- 탭 2: 면접(개선 UX) -----------------
            with gr.Tab("2) 면접"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### 현재 질문", elem_classes=["sticky-top"])
                        cur_q = gr.Markdown(value="**현재 질문이 없습니다.**", elem_classes=["sticky-top"])
                        answer = gr.Textbox(lines=8, placeholder="여기에 답변을 입력하세요 (숫자·지표·역할·결과 포함)")
                        with gr.Row():
                            btn_score = gr.Button("채점(STAR-C)")
                            btn_follow = gr.Button("꼬리질문 생성")
                            btn_next = gr.Button("다음 메인 질문")
                        # 면접 탭에도 리포트 버튼 제공
                        report_note = gr.Textbox(label="리포트 메모(선택)", lines=2, placeholder="발표/기록용 메모")
                        btn_report_here = gr.Button("리포트 생성 → 리포트 탭으로 이동")

                    with gr.Column(scale=1):
                        gr.Markdown("#### 히스토리 (최근순)")
                        history_panel = gr.HTML(render_history_html([]))

                # UX 스크립트 주입용 숨은 HTML
                ux_html = gr.HTML(visible=False, value="")

            # ----------------- 탭 3: 리포트 -----------------
            with gr.Tab("3) 리포트"):
                gr.Markdown("### 📊 리포트 미리보기")
                report_md = gr.Markdown("")
                report_file = gr.File(label="리포트 파일(.md)", interactive=False)

        # ============ 이벤트 바인딩 ============
        btn_start.click(
            start_interview,
            inputs=[context, difficulty, max_main, st_plan, st_history],
            outputs=[st_plan, st_history, cur_q, history_panel, ux_html]
        )

        btn_score.click(
            score_answer,
            inputs=[answer, st_plan, st_history],
            outputs=[st_history, history_panel, ux_html]
        )

        btn_follow.click(
            make_followup,
            inputs=[context, st_plan, st_history],
            outputs=[st_plan, st_history, cur_q, history_panel, ux_html]
        )

        btn_next.click(
            next_main,
            inputs=[context, st_plan, st_history],
            outputs=[st_plan, st_history, cur_q, history_panel, ux_html]
        )

        btn_report_here.click(
            make_report_and_go,
            inputs=[st_history, report_note],
            outputs=[report_file, report_md, ux_html]
        )

    return demo


if __name__ == "__main__":
    # uv run python main.py  (dotenvx로 환경주입 가정)
    app = build_app()
    app.queue().launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT","7860")))
