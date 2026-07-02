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


def _info_gain(slot: Slot, state: ClinicalState, lr_table: dict) -> float:
    """score(s) = Σ_d |log(LR_{s,d})| · P(d),d 取 differential 中条目。"""
    score = 0.0
    items = state.differential.must_not_miss + state.differential.most_likely
    for item in items:
        lr = lr_table.get((slot.name, item.condition))
        if lr and lr > 0:
            score += abs(math.log(lr)) * item.probability
    return score


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
