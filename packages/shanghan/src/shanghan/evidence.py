"""EvidenceVerifier — branch 级证据回源(评审 P1-3 / 第六条修复)。

不再只验证 `term in full_text`,而是:
  条件必须出现在 branch.condition_span;
  方剂结论必须出现在 branch.conclusion_span;
  evidence/condition/conclusion offsets 必须与条文原文逐字对齐。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shanghan.corpus import Clause
from shanghan.rules import InitialRule


@dataclass
class EvidenceReport:
    ok: bool
    problems: list[str] = field(default_factory=list)


class EvidenceVerifier:
    def __init__(self, corpus_index: dict[str, Clause]):
        self._index = corpus_index

    def verify(self, rule: InitialRule) -> EvidenceReport:
        problems: list[str] = []
        clause = self._index.get(rule.clause_id)
        if clause is None:
            return EvidenceReport(False, [f"clause_id 不存在: {rule.clause_id}"])

        s, e = rule.evidence_offsets
        if not (0 <= s <= e <= len(clause.text)) or clause.text[s:e] != rule.evidence_span:
            problems.append("evidence_offsets 与条文原文不对齐")
        if rule.evidence_span not in clause.text:
            problems.append("evidence_span 不在条文原文中")

        cs, ce = rule.condition_offsets
        if rule.condition_span and (
            not (0 <= cs <= ce <= len(clause.text))
            or clause.text[cs:ce] != rule.condition_span
        ):
            problems.append("condition_offsets 与条文原文不对齐")

        for term in rule.condition_terms():
            if term not in rule.condition_span:
                problems.append(f"条件不在 condition_span 内: {term}")
        for term in rule.if_conditions.get("optional_symptoms", []):
            if term not in rule.condition_span:
                problems.append(f"或然条件不在 condition_span 内: {term}")
        prior = rule.if_conditions.get("prior_treatment")
        if prior and prior not in rule.condition_span:
            problems.append(f"误治前置不在 condition_span 内: {prior}")

        formula = rule.then_conclusions.get("formula")
        if formula and formula not in rule.conclusion_span:
            problems.append(f"方剂不在 conclusion_span 内: {formula}")
        strength = rule.then_conclusions.get("strength")
        if strength and strength not in rule.conclusion_span:
            problems.append(f"强度标记不在 conclusion_span 内: {strength}")
        forbidden = rule.then_conclusions.get("forbidden_formula")
        if forbidden and forbidden not in rule.conclusion_span:
            problems.append(f"禁忌方剂不在 conclusion_span 内: {forbidden}")

        return EvidenceReport(ok=not problems, problems=problems)
