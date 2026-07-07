"""演示会话层 — 无 UI/语音依赖的纯逻辑,供 Gradio 端与 CI 测试共用。

安全语义与引擎一致: 患者可见文本只来自引擎动作(固定话术/模板问句/
确认提示),不经任何未治理的 LLM 路径;硬升级/心理危机后会话锁定。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from apps.colab_demo.bootstrap import setup_paths

setup_paths()

from hermes_contracts import ConsentFlags, Phase  # noqa: E402
from hermes_loop.engine import LoopEngine  # noqa: E402
from hermes_loop.skill_loader import load_skill  # noqa: E402

SKILLS = ("emergency_triage", "oncology_bone_metastasis")

_DISCLAIMER = (
    "本系统为工程演示(PENDING_PHYSICIAN_REVIEW),不构成诊断或处方;"
    "紧急情况请立即拨打120。"
)


@dataclass
class DemoSession:
    engine: LoopEngine
    skill_name: str
    turns: int = 0
    locked_reason: str | None = None


@dataclass
class TurnReply:
    reply_text: str
    actions: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    terminal: bool = False
    banner: str = ""


def new_session(skill_name: str = "emergency_triage") -> DemoSession:
    if skill_name not in SKILLS:
        raise ValueError(f"未知 skill: {skill_name}")
    engine = LoopEngine(
        encounter_id=f"demo_{uuid.uuid4().hex[:12]}",
        skill=load_skill(skill_name),
    )
    engine.begin(ConsentFlags(recording=True, retention=True))
    return DemoSession(engine=engine, skill_name=skill_name)


def state_view(session: DemoSession) -> dict:
    s = session.engine.state
    return {
        "phase": s.phase.value,
        "question_count": s.question_count,
        "escalation": s.escalation.value if s.escalation else None,
        "red_flag_hits": list(s.red_flag_hits),
        "degraded_mode": s.degraded_mode,
        "symptoms": [
            {"concept": x.concept_id, "status": x.status.value}
            for x in s.symptoms
        ],
        "must_not_miss": [
            {"condition": i.condition, "status": i.status,
             "pending": list(i.discriminators_pending)}
            for i in s.differential.must_not_miss
        ],
        "disclaimer": _DISCLAIMER,
    }


def _compose_reply(actions: list[dict]) -> tuple[str, str]:
    """把引擎动作序列组装为患者可读回复;返回 (回复文本, 横幅提示)。"""
    parts: list[str] = []
    banner = ""
    for a in actions:
        kind = a["type"]
        if kind in ("escalation", "psych_script", "psych_hold"):
            parts.append(a["text"])
            banner = (
                "⚠️ 已升级人工处理" if kind == "escalation"
                else "🤝 已转人工关怀通道"
            )
        elif kind == "notify_human":
            continue  # 内部动作,不直接播报
        elif kind == "reask":
            parts.append(a["prompt"])
        elif kind == "clarify":
            options = "、".join(a.get("options", []))
            parts.append(
                f"想和您确认一下,您说的「{a['term']}」更接近哪一种: {options}?"
            )
        elif kind in ("confirm", "readback"):
            parts.append(a["prompt"])
        elif kind == "question":
            parts.append(a["text"])
        elif kind == "awaiting_doctor":
            parts.append(
                "感谢您的配合,信息已经采集完成,接下来由医生审核。"
                "请稍候,不要离开。"
            )
    return " ".join(p for p in parts if p), banner


def patient_turn(
    session: DemoSession, text: str, asr_confidence: float = 0.95
) -> TurnReply:
    text = (text or "").strip()
    if not text:
        return TurnReply(reply_text="抱歉,我没有收到内容,请再说一次。",
                         state=state_view(session))
    phase = session.engine.state.phase
    if phase == Phase.ESCALATION:
        return TurnReply(
            reply_text="当前情况已转人工处理,请按刚才的提示立即就医;"
                       "如症状加重请直接拨打120。",
            state=state_view(session), terminal=True,
            banner="⚠️ 已升级人工处理",
        )
    if phase == Phase.END:
        return TurnReply(
            reply_text="本次预问诊已结束。如需继续,请开始新会话。",
            state=state_view(session), terminal=True,
        )
    res = session.engine.step(text, asr_confidence=asr_confidence)
    session.turns += 1
    reply, banner = _compose_reply(res.actions)
    terminal = session.engine.state.phase in (
        Phase.ESCALATION, Phase.PSYCH_RISK, Phase.DOCTOR_REVIEW, Phase.END,
    )
    if not reply:
        reply = "好的,我记录下来了。"
    return TurnReply(
        reply_text=reply, actions=res.actions,
        state=state_view(session), terminal=terminal, banner=banner,
    )


# ------------------------------------------------------------------ 医生端
def doctor_view(session: DemoSession,
                dose_drugs: tuple[str, ...] = ()) -> dict:
    """医生端视图 = compose_doctor_output + ManagerAgent 受限会诊段。

    会诊经 hermes_agents.orchestrator 走主链路(复杂度路由→specialists
    →合成→审计),LLM 不可用时确定性 fallback,失败不阻断基础视图。
    """
    from hermes_compose.composer import compose_doctor_output

    view = compose_doctor_output(
        session.engine.state, dose_advisory_drugs=tuple(dose_drugs)
    )
    try:
        from hermes_agents.orchestrator import run_doctor_consult

        view["agent_consult"] = run_doctor_consult(session.engine)
    except Exception as e:  # 会诊层故障降级: 基础视图仍完整可用
        view["agent_consult"] = {"error": f"consult_unavailable:{type(e).__name__}"}
    return view


def doctor_decide(
    session: DemoSession, decision: str = "adopt",
    doctor_id: str = "demo_doctor",
    resolutions: dict[str, str] | None = None,
) -> dict:
    return session.engine.doctor_review(
        decision=decision, doctor_id=doctor_id,
        resolved_conditions=resolutions or {},
    )


# ------------------------------------------------------------------ 伤寒问答
def shanghan_ask(question: str, role: str = "doctor") -> dict:
    from shanghan.runtime import default_rag

    return default_rag().ask(question, role=role)


# ------------------------------------------------------------------ 评测面板
def run_eval(kind: str) -> dict:
    if kind == "redteam":
        from evals.redteam.run import run_redteam

        return run_redteam()
    if kind.startswith("selfplay:"):
        from evals.simulation.selfplay import run_selfplay

        return run_selfplay(kind.split(":", 1)[1])
    if kind.startswith("replay:"):
        from evals.simulation.replay import run_skill

        return run_skill(kind.split(":", 1)[1])
    if kind == "shanghan_stats":
        from shanghan.runtime import default_result

        return default_result().stats
    if kind == "calibration":
        from evals.calibration.shanghan_gold import evaluate

        return evaluate()
    raise ValueError(f"未知评测项: {kind}")
