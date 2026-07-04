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
    #: 提问负担(患者时间/认知成本相对值,MAI-DxO Dr. Stewardship 语义)
    cost: float = 1.0


#: 信息增益停问阈值(bits): 最优候选低于此值时选问让位给模板必填,
#: 避免边际价值趋零的追问(ACTMED/MediQ 的 VoI 停止准则)
EIG_EPS = 0.01
#: 置信停问阈值: 后验最高鉴别概率达到 τ 后信息增益分支不再选问
#: (Calibrate-Then-Act 承诺规则);红旗与 must_not_miss 分支不受影响
CONFIDENCE_TAU = 0.95


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


def _categorical_entropy(dist: dict[str, float]) -> float:
    return -sum(p * math.log2(p) for p in dist.values() if p > 0.0)


def _categorical_prior(posteriors: dict[str, float]) -> dict[str, float]:
    """把各鉴别条目的边际概率折算为类别分布(含 other 残差质量)。"""
    total = sum(posteriors.values())
    other = max(0.0, 1.0 - total)
    z = total + other
    if z <= 0.0:
        return {}
    dist = {c: p / z for c, p in posteriors.items() if p > 0.0}
    if other > 0.0:
        dist["__other__"] = other / z
    return dist


def _expected_information_gain(
    slot: Slot, posteriors: dict[str, float], lr_table: dict
) -> float:
    """EIG(s) = I(D; A_s) = H_cat(q) - E_answer[H_cat(q|answer)]。

    把鉴别 D 建模为单一类别变量(BED-LLM, arXiv:2508.21184 证明须用联合
    目标真互信息): 逐条目独立二值 MI 求和会对"弱触多病"的槽位重复计分,
    系统性高估宽泛问题。无 LR 项的条目与 other 残差取 L=1(P(yes|·)=0.5,
    无信息);LR 全为 1 时 EIG 严格为 0;互信息非负有界(≤H_cat(q))。
    """
    q = _categorical_prior(posteriors)
    if len(q) < 2:
        return 0.0

    def p_yes_given(condition: str) -> float:
        lr = lr_table.get((slot.name, condition))
        if not lr or lr <= 0:
            return 0.5
        return lr / (1.0 + lr)

    p_yes = sum(q_c * p_yes_given(c) for c, q_c in q.items())
    if p_yes <= 0.0 or p_yes >= 1.0:
        return 0.0
    post_yes = {c: q_c * p_yes_given(c) / p_yes for c, q_c in q.items()}
    post_no = {
        c: q_c * (1.0 - p_yes_given(c)) / (1.0 - p_yes) for c, q_c in q.items()
    }
    gain = _categorical_entropy(q) - (
        p_yes * _categorical_entropy(post_yes)
        + (1.0 - p_yes) * _categorical_entropy(post_no)
    )
    return max(0.0, gain)


def _answer_balance(slot: Slot, posteriors: dict[str, float],
                    lr_table: dict) -> float:
    """|P(yes)-P(no)|: 越接近 0 问题越"平衡切分"假设空间(UoT,
    arXiv:2402.03271),作为 EIG 近并列时的二级决胜键。"""
    q = _categorical_prior(posteriors)
    if not q:
        return 1.0
    p_yes = 0.0
    for c, q_c in q.items():
        lr = lr_table.get((slot.name, c))
        p_yes += q_c * (lr / (1.0 + lr) if lr and lr > 0 else 0.5)
    return abs(2.0 * p_yes - 1.0)


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
        post = posterior_probabilities(state, lr_table)
        p_top = max(post.values(), default=0.0)
        # 置信停问: 鉴别已足够确定时不再为信息增益追问,让位模板必填
        if p_top < CONFIDENCE_TAU:
            scored = []
            for s in open_slots:
                gain = _expected_information_gain(s, post, lr_table)
                if gain <= EIG_EPS:
                    continue
                # 单位负担信息增益(EIG/cost)为主键;
                # 平衡切分度(UoT)为二级决胜;priority/name 保证确定性
                scored.append((
                    -gain / max(s.cost, 1e-9),
                    _answer_balance(s, post, lr_table),
                    s.priority,
                    s.name,
                    s,
                ))
            if scored:
                scored.sort(key=lambda x: x[:4])
                return pick([scored[0][4]], "information_gain")

    required = [s for s in open_slots if s.required]
    if required:
        return pick(required, "template_required")
    return pick(open_slots, "template")
