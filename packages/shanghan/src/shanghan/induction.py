"""归纳层: 方证归纳 / 六经锚定 / 鉴别对。

- FormulaPatternInducer 的核心证候判别采用特异性加权
  core_score = IDF特异性 × 主之权重 × 频次因子(评审 P1-5),
  而非简单的 ≥2 次频次阈值;
- SixChannelInducer 是在后世六经亚型框架约束下对原文证据的自动锚定,
  标注 source_level="posthoc_induction",不得表述为"系统自主发现"。
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from shanghan.review import ApprovedRule

_STRENGTH_WEIGHT = {"主之": 1.0, "宜": 0.8, "可与": 0.6, "与": 0.5, "属": 0.5}
CORE_THRESHOLD = 0.35


@dataclass
class FormulaPattern:
    formula: str
    core_symptoms: list[str] = field(default_factory=list)
    associated_symptoms: list[str] = field(default_factory=list)
    pulses: list[str] = field(default_factory=list)
    supporting_clauses: list[str] = field(default_factory=list)
    core_scores: dict[str, float] = field(default_factory=dict)
    contraindications: list[str] = field(default_factory=list)
    source_level: str = "posthoc_induction"


class FormulaPatternInducer:
    def induce(self, approved: list[ApprovedRule]) -> dict[str, FormulaPattern]:
        usable = [
            ar for ar in approved
            if ar.release_level != "rejected"
            and ar.rule.rule_type in ("formula_pattern_rule", "mistreatment_rule")
            and ar.rule.then_conclusions.get("formula")
        ]
        # 文档频率: 某症状出现在多少个不同方剂的规则里(特异性反比)
        term_formulas: dict[str, set[str]] = defaultdict(set)
        for ar in usable:
            f = ar.rule.then_conclusions["formula"]
            for t in ar.rule.condition_terms():
                term_formulas[t].add(f)
        n_formulas = max(len({ar.rule.then_conclusions["formula"] for ar in usable}), 1)

        by_formula: dict[str, list[ApprovedRule]] = defaultdict(list)
        for ar in usable:
            by_formula[ar.rule.then_conclusions["formula"]].append(ar)

        patterns: dict[str, FormulaPattern] = {}
        for formula, rules in by_formula.items():
            term_freq: dict[str, int] = defaultdict(int)
            term_strength: dict[str, float] = defaultdict(float)
            pulses: set[str] = set()
            clauses: list[str] = []
            optional: set[str] = set()
            for ar in rules:
                clauses.append(ar.rule.clause_id)
                w = _STRENGTH_WEIGHT.get(
                    ar.rule.then_conclusions.get("strength") or "", 0.5
                )
                for t in ar.rule.if_conditions.get("symptoms", []):
                    term_freq[t] += 1
                    term_strength[t] = max(term_strength[t], w)
                for t in ar.rule.if_conditions.get("optional_symptoms", []):
                    optional.add(t)
                for p in ar.rule.if_conditions.get("pulses", []):
                    pulses.add(p)

            core_scores: dict[str, float] = {}
            for t, freq in term_freq.items():
                specificity = math.log(
                    (1 + n_formulas) / (1 + len(term_formulas[t]))
                ) / math.log(1 + n_formulas)
                freq_factor = min(freq, 2) / 2
                core_scores[t] = round(
                    specificity * term_strength[t] * (0.5 + 0.5 * freq_factor), 4
                )
            core = sorted(
                [t for t, s in core_scores.items() if s >= CORE_THRESHOLD],
                key=lambda t: -core_scores[t],
            )
            associated = sorted(
                [t for t in term_freq if t not in core] + sorted(optional),
                key=str,
            )
            patterns[formula] = FormulaPattern(
                formula=formula,
                core_symptoms=core,
                associated_symptoms=associated,
                pulses=sorted(pulses),
                supporting_clauses=sorted(set(clauses)),
                core_scores=core_scores,
            )

        for ar in approved:
            if (
                ar.release_level != "rejected"
                and ar.rule.rule_type == "contraindication_rule"
            ):
                f = ar.rule.then_conclusions.get("forbidden_formula")
                if f in patterns:
                    patterns[f].contraindications.append(ar.rule.condition_span)
        return patterns


# 后世六经亚型框架(开发者预置理论框架,非系统自主发现)
CHANNEL_SUBTYPES = {
    "taiyang": ["太阳中风(表虚)", "太阳伤寒(表实)", "太阳蓄水"],
    "yangming": ["阳明经证", "阳明腑实"],
    "shaoyang": ["少阳半表半里"],
    "taiyin": ["太阴虚寒"],
    "shaoyin": ["少阴寒化", "少阴热化"],
    "jueyin": ["厥阴寒热错杂"],
}


@dataclass
class ChannelSummary:
    channel: str
    outline_clause: str | None
    outline_conditions: list[str]
    formulas: list[str]
    subtypes: list[str]
    source_level: str = "posthoc_induction"


class SixChannelInducer:
    """在后世六经亚型框架约束下,对原文证据做自动锚定与结构化归纳。"""

    def induce(self, approved: list[ApprovedRule]) -> dict[str, ChannelSummary]:
        outlines = {
            ar.rule.then_conclusions["channel"]: ar
            for ar in approved
            if ar.release_level != "rejected"
            and ar.rule.rule_type == "channel_outline_rule"
        }
        # 按条文 channel 字段锚定(语料携带篇章信息)
        return self._summaries(outlines, approved)

    @staticmethod
    def _summaries(outlines, approved) -> dict[str, ChannelSummary]:
        from shanghan.corpus import corpus_index

        index = corpus_index()
        formulas_by_channel: dict[str, set[str]] = defaultdict(set)
        for ar in approved:
            if ar.release_level == "rejected":
                continue
            f = ar.rule.then_conclusions.get("formula")
            if f:
                clause = index.get(ar.rule.clause_id)
                if clause:
                    formulas_by_channel[clause.channel].add(f)
        out: dict[str, ChannelSummary] = {}
        for channel, subtypes in CHANNEL_SUBTYPES.items():
            outline = outlines.get(channel)
            out[channel] = ChannelSummary(
                channel=channel,
                outline_clause=outline.rule.clause_id if outline else None,
                outline_conditions=outline.rule.condition_terms() if outline else [],
                formulas=sorted(formulas_by_channel.get(channel, set())),
                subtypes=subtypes,
            )
        return out


@dataclass
class DifferentialPair:
    formula_a: str
    formula_b: str
    shared: list[str]
    discriminators_a: list[str]  # 支持 a 的鉴别点
    discriminators_b: list[str]


class DifferentialInducer:
    def induce(self, patterns: dict[str, FormulaPattern]) -> list[DifferentialPair]:
        pairs: list[DifferentialPair] = []
        names = sorted(patterns)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa, pb = patterns[a], patterns[b]
                fa = set(pa.core_symptoms) | set(pa.associated_symptoms) | set(pa.pulses)
                fb = set(pb.core_symptoms) | set(pb.associated_symptoms) | set(pb.pulses)
                shared = fa & fb
                if not shared:
                    continue
                pairs.append(
                    DifferentialPair(
                        formula_a=a,
                        formula_b=b,
                        shared=sorted(shared),
                        discriminators_a=sorted(fa - fb),
                        discriminators_b=sorted(fb - fa),
                    )
                )
        return pairs
