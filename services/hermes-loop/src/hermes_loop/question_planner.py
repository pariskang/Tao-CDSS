"""价值驱动选问器(协议 L3.2)。

优先级: 红旗槽位 > must_not_miss 判别问题 > 信息增益最大槽位 > 模板必填 > 条件槽位。
约束: 每轮一问;总问数上限 MAX_QUESTIONS;已答禁重问;敏感槽位先说明理由。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from hermes_contracts import MAX_QUESTIONS, ClinicalState


@dataclass(frozen=True)
class Slot:
    name: str
    question: str
    priority: int = 100
    required: bool = False
    red_flag: bool = False
    sensitive: bool = False
    reason: str = ""
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    slot: str
    text: str
    rationale: str


# ---------------------------------------------------------------- 贝叶斯选问
# 理论: 序贯诊断中问题的价值 = 预期后验熵减(期望信息增益/互信息),
# AMIE(Nature 2025)与 MAI-DxO(2025)均以此类准则驱动问诊/检验选择。
# 两点模型: 已知 LR+ = L 且近似 LR- = 1/L 时,唯一相容的条件概率为
#   P(yes|d) = L/(1+L),  P(yes|~d) = 1/(1+L)
# (由 LR+ = P(yes|d)/P(yes|~d) 与 LR- = P(no|d)/P(no|~d) 联立解出),
# 因此 EIG 有解析闭式,纯确定性可测试。LR 数值属临床数据,PENDING 医师审定。


def _binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _posterior_yes(p: float, lr: float) -> float:
    """观察到 yes 后的后验: p·L / (p·L + (1-p))。"""
    return p * lr / (p * lr + (1 - p))


def _posterior_no(p: float, lr: float) -> float:
    """观察到 no 后的后验(两点模型 LR- = 1/L): p / (p + (1-p)·L)。"""
    return p / (p + (1 - p) * lr)


def posterior_probabilities(
    state: ClinicalState, lr_table: dict
) -> dict[str, float]:
    """从 skill 先验出发,按已答槽位折算各鉴别条目的后验概率。

    纯函数(每轮由 answered_slots 全量重算),不写 clinical_state——
    概率非溯源字段,保持契约不变(硬规则5)。
    present → yes 更新;denied → no 更新;unknown/probably_denied 不更新。
    """
    items = state.differential.must_not_miss + state.differential.most_likely
    post = {i.condition: i.probability for i in items}
    for (slot_name, condition), lr in lr_table.items():
        if condition not in post or not lr or lr <= 0 or lr == 1.0:
            continue
        answer = state.answered_slots.get(slot_name)
        if answer == "present":
            post[condition] = _posterior_yes(post[condition], lr)
        elif answer == "denied":
            post[condition] = _posterior_no(post[condition], lr)
    return post


def _expected_information_gain(
    slot: Slot, posteriors: dict[str, float], lr_table: dict
) -> float:
    """EIG(s) = Σ_d I(D_d; A_s) = Σ_d [H(p_d) - E_answer H(p_d|answer)]。

    互信息非负;LR=1 时严格为 0(无信息问题不加分);LR 越极端增益越大。
    """
    gain = 0.0
    for condition, p in posteriors.items():
        lr = lr_table.get((slot.name, condition))
        if not lr or lr <= 0 or lr == 1.0 or p <= 0.0 or p >= 1.0:
            continue
        p_yes_marginal = (p * lr + (1 - p)) / (1 + lr)
        h_prior = _binary_entropy(p)
        h_post = (
            p_yes_marginal * _binary_entropy(_posterior_yes(p, lr))
            + (1 - p_yes_marginal) * _binary_entropy(_posterior_no(p, lr))
        )
        gain += max(0.0, h_prior - h_post)
    return gain


def _info_gain(slot: Slot, state: ClinicalState, lr_table: dict) -> float:
    """价值驱动选问分数 = 预期信息增益(替换旧 Σ|logLR|·P(d) 启发式)。"""
    return _expected_information_gain(
        slot, posterior_probabilities(state, lr_table), lr_table
    )


def next_question(
    state: ClinicalState,
    slots: list[Slot],
    lr_table: dict | None = None,
) -> Question | None:
    if state.question_count >= MAX_QUESTIONS:
        return None
    closed = set(state.asked_slots) | set(state.answered_slots)
    # "记不清"(unknown)不永久关闭必填槽位: 允许换一次问法再问,
    # 最多重问一次(asked 计数≥2 后不再重开),避免无限循环
    reopen = {
        name
        for name, status in state.answered_slots.items()
        if status == "unknown"
        and state.asked_slots.count(name) < 2
        and any(s.name == name and s.required for s in slots)
    }
    open_slots = [s for s in slots if s.name not in closed or s.name in reopen]
    if not open_slots:
        return None

    def pick(candidates: list[Slot], rationale: str) -> Question:
        slot = sorted(candidates, key=lambda s: s.priority)[0]
        text = slot.question
        if slot.sensitive and slot.reason:
            text = f"{slot.reason}{text}"
        return Question(slot=slot.name, text=text, rationale=rationale)

    red = [s for s in open_slots if s.red_flag]
    if red:
        return pick(red, "red_flag")

    disc_names = {
        d
        for item in state.differential.blocking_items()
        for d in item.discriminators_pending
    }
    disc = [s for s in open_slots if s.name in disc_names]
    if disc:
        return pick(disc, "must_not_miss")

    if lr_table:
        scored = [(s, _info_gain(s, state, lr_table)) for s in open_slots]
        scored = [(s, sc) for s, sc in scored if sc > 0]
        if scored:
            slot = max(scored, key=lambda x: x[1])[0]
            return pick([slot], "information_gain")

    required = [s for s in open_slots if s.required]
    if required:
        return pick(required, "template_required")
    return pick(open_slots, "template")
