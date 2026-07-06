"""字段级一致性门控 — 结构化输出的离散语义熵投票。

理论: semantic entropy(Farquhar et al., Nature 2024)按语义等价类对
k 次采样聚类,熵高即幻觉风险高。结构化输出天然定义等价类,且字段级
计票比整对象等价类更细(整对象投票会因一个次要字段措辞差异误杀整体,
CMR-EXTR 2025 的字段级稳定性结论)。

设计约束:
  - 纯函数、零网络零 LLM,完全确定性;
  - 三档字段策略: safety(须全票一致)/ standard(须达法定多数)/
    info(仅记录不判决);
  - 返回 medoid 真实样本(与各字段众数吻合度最高的原样本),绝不逐字段
    拼装——拼装可能产生违反跨字段不变量的"缝合怪";
  - 门控只有否决权: PASS 不豁免 dose_egress / redflag / contracts
    的任何下游校验("一致 ≠ 正确",只作否决)。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel

PASS, DEGRADE, FAIL = "pass", "degrade", "fail"


@dataclass
class GateResult:
    decision: str  # pass | degrade | fail
    chosen_index: int | None
    field_report: dict[str, dict] = field(default_factory=dict)
    min_agreement: float = 0.0


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _field_entropy(counts: Counter, k: int) -> float:
    return -sum((c / k) * math.log2(c / k) for c in counts.values() if c)


def gate(
    samples: list[BaseModel],
    safety_fields: tuple[str, ...] = (),
    info_fields: tuple[str, ...] = (),
    quorum_ratio: float = 0.6,
) -> GateResult:
    """对 k 个同 schema 样本做字段级 quorum 判决。

    safety 字段: 任一分歧即 FAIL(全票一致才过);
    standard 字段: 众数 < ⌈quorum_ratio·k⌉ 即 DEGRADE;
    info 字段: 仅记录熵/票数,不参与判决。
    """
    if not samples:
        return GateResult(decision=FAIL, chosen_index=None)
    k = len(samples)
    if k == 1:
        return GateResult(decision=PASS, chosen_index=0, min_agreement=1.0)

    field_names = list(type(samples[0]).model_fields)
    quorum = math.ceil(quorum_ratio * k)
    decision = PASS
    report: dict[str, dict] = {}
    modal_values: dict[str, str] = {}
    min_agreement = 1.0

    for name in field_names:
        values = [_canon(getattr(s, name)) for s in samples]
        counts = Counter(values)
        # 众数取(票数, canonical 值字典序)最大者,保证确定性
        modal_value, modal_n = max(
            counts.items(), key=lambda kv: (kv[1], kv[0])
        )
        modal_values[name] = modal_value
        tier = (
            "safety" if name in safety_fields
            else "info" if name in info_fields
            else "standard"
        )
        report[name] = {
            "tier": tier,
            "modal_votes": modal_n,
            "classes": len(counts),
            "entropy_bits": round(_field_entropy(counts, k), 4),
            "agreement": round(modal_n / k, 4),
        }
        if tier == "info":
            continue
        min_agreement = min(min_agreement, modal_n / k)
        if tier == "safety" and modal_n < k:
            decision = FAIL
        elif tier == "standard" and modal_n < quorum and decision != FAIL:
            decision = DEGRADE

    # medoid: 与各(非 info)字段众数吻合度最高的真实样本;平票取最小下标
    def medoid_score(idx: int) -> int:
        return sum(
            1
            for name in field_names
            if report[name]["tier"] != "info"
            and _canon(getattr(samples[idx], name)) == modal_values[name]
        )

    chosen = max(range(k), key=lambda i: (medoid_score(i), -i))
    return GateResult(
        decision=decision,
        chosen_index=chosen if decision != FAIL else None,
        field_report=report,
        min_agreement=round(min_agreement, 4),
    )
