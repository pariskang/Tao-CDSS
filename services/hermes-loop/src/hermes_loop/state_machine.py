"""SCOPE 确定性临床状态机(协议 L3.1)。

不变量A: E0/E1 触发后不可被对话降级,只有人工(actor="human")可降级;
PSYCH_RISK 任意相可入,只出不返自动流程(离开需人工)。
"""
from __future__ import annotations

from hermes_contracts import ESCALATION_SEVERITY, ClinicalState, EscalationLevel, Phase

TRANSITIONS: dict[Phase, list[Phase]] = {
    Phase.CONSENT: [Phase.IDENTITY_PROXY],
    Phase.IDENTITY_PROXY: [Phase.CHIEF_COMPLAINT],
    Phase.CHIEF_COMPLAINT: [Phase.RED_FLAG],
    Phase.RED_FLAG: [Phase.HPI, Phase.ESCALATION],
    Phase.HPI: [Phase.HPI, Phase.PMH_MED, Phase.ESCALATION],
    Phase.PMH_MED: [Phase.SPECIALTY, Phase.ESCALATION],
    Phase.SPECIALTY: [Phase.DIFFERENTIAL, Phase.ESCALATION],
    Phase.DIFFERENTIAL: [Phase.HPI, Phase.EVIDENCE],  # 回HPI=补判别问题
    Phase.EVIDENCE: [Phase.VERIFY],
    Phase.VERIFY: [Phase.DOCTOR_REVIEW, Phase.EVIDENCE],
    Phase.DOCTOR_REVIEW: [Phase.SUMMARY],
    Phase.SUMMARY: [Phase.END],
    Phase.ESCALATION: [Phase.END],
}

_HARD_LEVELS = (EscalationLevel.E0_CALL_120, EscalationLevel.E1_ER_NOW)


class TransitionError(Exception):
    pass


def transition(state: ClinicalState, to: Phase, actor: str = "system") -> ClinicalState:
    cur = state.phase
    if to == cur:
        return state
    if to == Phase.PSYCH_RISK:
        pass  # 任意相可入
    elif cur == Phase.PSYCH_RISK:
        if actor != "human":
            raise TransitionError("PSYCH_RISK 只能由人工动作离开")
    elif to == Phase.ESCALATION:
        if cur == Phase.END:
            raise TransitionError("END 后不可升级")
    elif cur == Phase.ESCALATION:
        if state.escalation in _HARD_LEVELS and actor != "human" and to != Phase.END:
            raise TransitionError("E0/E1 升级不可被对话降级,只有人工可降级")
        if actor != "human" and to != Phase.END:
            raise TransitionError(f"ESCALATION 不允许自动迁移到 {to}")
    elif to not in TRANSITIONS.get(cur, []):
        raise TransitionError(f"非法相变: {cur} -> {to}")
    state.phase = to  # pydantic validate_assignment 触发不变量B校验
    return state


def set_escalation(
    state: ClinicalState, level: EscalationLevel, actor: str = "system"
) -> ClinicalState:
    cur = state.escalation
    if cur in _HARD_LEVELS:
        if (
            ESCALATION_SEVERITY[level.value] > ESCALATION_SEVERITY[cur.value]
            and actor != "human"
        ):
            raise TransitionError("E0/E1 升级等级只有人工可降级")
    state.escalation = level
    return state
