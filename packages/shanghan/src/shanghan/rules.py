"""初始规则模型与抽取(branch 级证据,评审 P1-2)。

每条规则携带:
  evidence_span + evidence_offsets(分支区间,而非整条条文)
  condition_span / conclusion_span / branch_id
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from shanghan.brancher import Branch, ClauseBrancher
from shanghan.corpus import Clause
from shanghan.entities import EntityExtractor

RULE_TYPES = (
    "formula_pattern_rule",
    "contraindication_rule",
    "channel_outline_rule",
    "mistreatment_rule",
)

_OUTLINE_RE = re.compile(r"(太阳|阳明|少阳|太阴|少阴|厥阴)之为病")

CHANNEL_NAMES = {
    "太阳": "taiyang", "阳明": "yangming", "少阳": "shaoyang",
    "太阴": "taiyin", "少阴": "shaoyin", "厥阴": "jueyin",
}


class InitialRule(BaseModel):
    rule_id: str
    rule_type: str = Field(pattern="^(" + "|".join(RULE_TYPES) + ")$")
    clause_id: str
    branch_id: str
    if_conditions: dict = {}  # {symptoms:[], pulses:[], optional_symptoms:[], absent:[], prior_treatment}
    then_conclusions: dict = {}  # {formula, strength} | {forbidden_formula} | {channel} | {outcome}
    evidence_span: str
    evidence_offsets: tuple[int, int]
    condition_span: str
    condition_offsets: tuple[int, int]
    conclusion_span: str
    conclusion_offsets: tuple[int, int]
    model_confidence: float = Field(default=0.8, ge=0, le=1)
    source_level: str = "clause_branch"

    def condition_terms(self) -> list[str]:
        return (
            list(self.if_conditions.get("symptoms", []))
            + list(self.if_conditions.get("pulses", []))
        )


class InitialRuleExtractor:
    def __init__(self):
        self._brancher = ClauseBrancher()
        self._entities = EntityExtractor()

    def extract_clause(self, clause: Clause) -> list[InitialRule]:
        rules: list[InitialRule] = []
        for branch in self._brancher.split(clause):
            rule = self._from_branch(clause, branch)
            if rule is not None:
                rules.append(rule)
        return rules

    def extract_corpus(self, clauses: list[Clause]) -> list[InitialRule]:
        rules: list[InitialRule] = []
        for c in clauses:
            rules.extend(self.extract_clause(c))
        return rules

    # ------------------------------------------------------------------
    def _from_branch(self, clause: Clause, branch: Branch) -> InitialRule | None:
        conditions = self._conditions(branch)
        base = dict(
            clause_id=clause.clause_id,
            branch_id=branch.branch_id,
            evidence_span=branch.text,
            evidence_offsets=(branch.start, branch.end),
            condition_span=branch.condition_span,
            condition_offsets=(branch.condition_start, branch.condition_end),
            conclusion_span=branch.action_span,
            conclusion_offsets=(branch.action_start, branch.action_end),
        )

        if branch.polarity == "indicated" and branch.formula:
            rule_type = (
                "mistreatment_rule" if branch.prior_treatment else "formula_pattern_rule"
            )
            if branch.prior_treatment:
                conditions["prior_treatment"] = branch.prior_treatment
            return InitialRule(
                rule_id=f"{branch.branch_id}_R",
                rule_type=rule_type,
                if_conditions=conditions,
                then_conclusions={"formula": branch.formula,
                                  "strength": branch.strength},
                **base,
            )
        if branch.polarity == "contraindicated" and branch.formula:
            return InitialRule(
                rule_id=f"{branch.branch_id}_R",
                rule_type="contraindication_rule",
                if_conditions=conditions,
                then_conclusions={"forbidden_formula": branch.formula},
                **base,
            )
        m = _OUTLINE_RE.search(branch.text)
        if m:
            return InitialRule(
                rule_id=f"{branch.branch_id}_R",
                rule_type="channel_outline_rule",
                if_conditions=conditions,
                then_conclusions={"channel": CHANNEL_NAMES[m.group(1)]},
                # 提纲规则的结论区间即"X之为病"短语
                conclusion_span=m.group(0),
                conclusion_offsets=(branch.start + m.start(), branch.start + m.end()),
                **{k: v for k, v in base.items()
                   if k not in ("conclusion_span", "conclusion_offsets")},
            )
        return None  # 纯观察分支暂不出规则(如"名为中风"命名条)

    def _conditions(self, branch: Branch) -> dict:
        mentions = self._entities.extract(
            branch.condition_span, branch.optional_markers
        )
        symptoms = [m.term for m in mentions
                    if m.kind == "symptom" and not m.negated and not m.optional]
        pulses = [m.term for m in mentions if m.kind == "pulse" and not m.negated]
        optional = [m.term for m in mentions if m.optional and not m.negated]
        absent = [m.term for m in mentions if m.negated]
        out: dict = {}
        if symptoms:
            out["symptoms"] = symptoms
        if pulses:
            out["pulses"] = pulses
        if optional:
            out["optional_symptoms"] = optional
        if absent:
            out["absent"] = absent
        return out
