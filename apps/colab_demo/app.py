"""Hermes-CDSS Gradio 演示端 — 多轮语音预问诊 + 医生工作台 + 伤寒问答 + 评测。

用法(Colab 见 notebooks/hermes_colab_demo.ipynb):
    pip install -e ".[demo,llm,dev]"   # 或按 notebook 逐项安装
    python -m apps.colab_demo.app --ngrok-token <TOKEN>

安全边界: 患者可见文本/语音全部来自引擎治理后的动作(固定话术/模板问句),
UI 层不生成任何医疗内容;TTS 只朗读已过 dose_egress/scope 的文本。
"""
from __future__ import annotations

import argparse
import json
import os

from apps.colab_demo.bootstrap import setup_paths

setup_paths()

import gradio as gr  # noqa: E402

from apps.colab_demo import session as S  # noqa: E402
from apps.colab_demo import voice as V  # noqa: E402

_TITLE = "Hermes-CDSS 演示端(工程占位,PENDING_PHYSICIAN_REVIEW)"
_DISCLAIMER = (
    "> ⚠️ 本演示为工程验证,临床规则未经执业医师审定,**不构成诊断或处方**;"
    "紧急情况请立即拨打 120。语音将被转写用于演示,请勿输入真实个人信息。"
)


def _fmt_history(history: list, user: str, reply: str) -> list:
    history = list(history or [])
    history.append({"role": "user", "content": user})
    history.append({"role": "assistant", "content": reply})
    return history


# ------------------------------------------------------------------ 患者端
def ui_new_session(skill_name: str):
    sess = S.new_session(skill_name)
    greeting = (
        "您好,我是预问诊助理,接下来我会问几个问题帮医生提前了解您的情况。"
        "请问您哪里不舒服?"
    )
    return (
        sess,
        [{"role": "assistant", "content": greeting}],
        V.synthesize(greeting),
        S.state_view(sess),
        "",
    )


def _turn(sess, history, text: str, conf: float):
    if sess is None:
        return sess, history, None, {}, "请先点击「开始新会话」。"
    reply = S.patient_turn(sess, text, asr_confidence=conf)
    history = _fmt_history(history, text, reply.reply_text)
    return (
        sess,
        history,
        V.synthesize(reply.reply_text),
        reply.state,
        reply.banner,
    )


def ui_text_turn(sess, history, text: str):
    return _turn(sess, history, text, 0.95) + ("",)


def ui_voice_turn(sess, history, audio_path):
    text, conf = V.transcribe(audio_path)
    if not text:
        return sess, history, None, (
            S.state_view(sess) if sess else {}
        ), "没有识别到语音,请重试。", None
    out = _turn(sess, history, text, conf)
    return out + (None,)


# ------------------------------------------------------------------ 医生端
def ui_doctor_view(sess, drugs_csv: str):
    if sess is None:
        return {"error": "请先在患者端完成一次会话"}
    drugs = tuple(d.strip() for d in (drugs_csv or "").split(",") if d.strip())
    return S.doctor_view(sess, dose_drugs=drugs)


def ui_doctor_decide(sess, decision: str, resolutions_json: str):
    if sess is None:
        return {"error": "无活动会话"}, None
    try:
        resolutions = (
            json.loads(resolutions_json) if resolutions_json.strip() else {}
        )
    except json.JSONDecodeError as e:
        return {"error": f"裁决 JSON 无法解析: {e}"}, None
    try:
        result = S.doctor_decide(sess, decision=decision,
                                 resolutions=resolutions)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}, None
    audio = None
    if not result.get("withheld") and result.get("summary"):
        audio = V.synthesize(result["summary"]["text"])
    return result, audio


# ------------------------------------------------------------------ 伤寒问答
def ui_shanghan(question: str, role: str):
    if not question.strip():
        return {"error": "请输入问题"}, None
    out = S.shanghan_ask(question, role=role)
    return out, V.synthesize(str(out.get("answer", "")))


def ui_shanghan_voice(audio_path, role: str):
    text, _conf = V.transcribe(audio_path)
    if not text:
        return "", {"error": "没有识别到语音"}, None
    out, audio = ui_shanghan(text, role)
    return text, out, audio


# ------------------------------------------------------------------ 评测
def ui_eval(kind: str):
    return S.run_eval(kind)


def build_demo() -> gr.Blocks:
    with gr.Blocks(title=_TITLE, theme=gr.themes.Soft()) as demo:
        gr.Markdown(f"# {_TITLE}\n{_DISCLAIMER}")
        sess_state = gr.State(None)

        with gr.Tab("🩺 患者预问诊(语音/文字)"):
            with gr.Row():
                skill_dd = gr.Dropdown(
                    choices=list(S.SKILLS), value="emergency_triage",
                    label="临床能力包(Skill)",
                )
                new_btn = gr.Button("开始新会话", variant="primary")
            banner_md = gr.Markdown("")
            chat = gr.Chatbot(label="问诊对话", height=380, type="messages")
            reply_audio = gr.Audio(label="语音回复", autoplay=True,
                                   interactive=False)
            with gr.Row():
                mic = gr.Audio(sources=["microphone"], type="filepath",
                               label="🎤 语音输入(说完自动识别)")
                with gr.Column():
                    text_in = gr.Textbox(label="或直接输入文字",
                                         placeholder="例: 咳嗽两天,有点发烧")
                    send_btn = gr.Button("发送")
            state_json = gr.JSON(label="临床状态(可审计视图)")

            new_btn.click(ui_new_session, [skill_dd],
                          [sess_state, chat, reply_audio, state_json,
                           banner_md])
            send_btn.click(ui_text_turn, [sess_state, chat, text_in],
                           [sess_state, chat, reply_audio, state_json,
                            banner_md, text_in])
            text_in.submit(ui_text_turn, [sess_state, chat, text_in],
                           [sess_state, chat, reply_audio, state_json,
                            banner_md, text_in])
            mic.stop_recording(ui_voice_turn, [sess_state, chat, mic],
                               [sess_state, chat, reply_audio, state_json,
                                banner_md, mic])

        with gr.Tab("👨‍⚕️ 医生工作台"):
            gr.Markdown("患者端到达 DOCTOR_REVIEW 后,此处审核并放行摘要。")
            drugs_in = gr.Textbox(
                label="剂量回填药物(逗号分隔,drug_safety 结构化回填)",
                placeholder="ibuprofen",
            )
            view_btn = gr.Button("生成医生端视图(SOAP/鉴别/证据/缺口)")
            doctor_json = gr.JSON(label="医生端输出")
            gr.Markdown("---")
            decision_radio = gr.Radio(
                ["adopt", "modify", "reject"], value="adopt", label="审核决定"
            )
            resolutions_tb = gr.Textbox(
                label='must_not_miss 裁决 JSON(可空),例 '
                      '{"spinal_cord_compression": "excluded"}',
            )
            decide_btn = gr.Button("执行医生审核", variant="primary")
            decide_json = gr.JSON(label="审核结果(withheld/患者摘要)")
            summary_audio = gr.Audio(label="患者摘要语音", autoplay=False,
                                     interactive=False)

            view_btn.click(ui_doctor_view, [sess_state, drugs_in],
                           [doctor_json])
            decide_btn.click(ui_doctor_decide,
                             [sess_state, decision_radio, resolutions_tb],
                             [decide_json, summary_audio])

        with gr.Tab("📜 伤寒论问答(Shanghan-Hermes)"):
            role_radio = gr.Radio(["doctor", "researcher", "patient"],
                                  value="doctor", label="角色(患者角色自动脱敏)")
            with gr.Row():
                sh_mic = gr.Audio(sources=["microphone"], type="filepath",
                                  label="🎤 语音提问")
                sh_q = gr.Textbox(label="问题",
                                  placeholder="桂枝汤和麻黄汤怎么鉴别?")
            sh_btn = gr.Button("提问", variant="primary")
            sh_json = gr.JSON(label="结构化回答(含条文证据/conformal 弃权)")
            sh_audio = gr.Audio(label="语音回答", autoplay=True,
                                interactive=False)

            sh_btn.click(ui_shanghan, [sh_q, role_radio],
                         [sh_json, sh_audio])
            sh_q.submit(ui_shanghan, [sh_q, role_radio], [sh_json, sh_audio])
            sh_mic.stop_recording(ui_shanghan_voice, [sh_mic, role_radio],
                                  [sh_q, sh_json, sh_audio])

        with gr.Tab("🛡️ 评测与系统状态"):
            gr.Markdown("全部评测确定性可复现;红队/自演指标是发布硬门禁。")
            eval_dd = gr.Dropdown(
                choices=[
                    "redteam",
                    "selfplay:emergency_triage",
                    "selfplay:oncology_bone_metastasis",
                    "replay:emergency_triage",
                    "replay:oncology_bone_metastasis",
                    "shanghan_stats",
                    "calibration",
                ],
                value="redteam", label="评测项",
            )
            eval_btn = gr.Button("运行")
            eval_json = gr.JSON(label="报告")
            eval_btn.click(ui_eval, [eval_dd], [eval_json])

    return demo


def launch(ngrok_token: str | None = None, port: int = 7860,
           share_fallback: bool = True) -> None:
    public_url = None
    token = ngrok_token or os.environ.get("NGROK_AUTHTOKEN")
    if token:
        try:
            from pyngrok import ngrok

            ngrok.set_auth_token(token)
            public_url = ngrok.connect(port, "http").public_url
            print(f"\n🌐 ngrok 公网地址: {public_url}\n")
        except Exception as e:  # ngrok 失败退回 gradio share
            print(f"ngrok 启动失败({e}),退回 gradio share 链接")
    demo = build_demo()
    demo.queue().launch(
        server_name="0.0.0.0", server_port=port,
        share=share_fallback and public_url is None,
        show_error=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ngrok-token", default=None)
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    launch(ngrok_token=args.ngrok_token, port=args.port)
