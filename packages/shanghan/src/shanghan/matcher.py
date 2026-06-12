"""FormulaMatcher — 症状/脉象 → 方证候选(医生端,仅供执业医师参考)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from shanghan.induction import FormulaPattern


@dataclass
class MatchResult:
    formula: str
    score: float
    matched_core: list[str] = field(default_factory=list)
    matched_associated: list[str] = field(default_factory=list)
    matched_pulses: list[str] = field(default_factory=list)
    missing_core: list[str] = field(default_factory=list)
    supporting_clauses: list[str] = field(default_factory=list)


class FormulaMatcher:
    def __init__(self, patterns: dict[str, FormulaPattern]):
        self._patterns = patterns

    def match(
        self, symptoms: list[str], pulses: list[str] | None = None, top_k: int = 3
    ) -> list[MatchResult]:
        pulses = pulses or []
        results: list[MatchResult] = []
        for formula, p in self._patterns.items():
            matched_core = [s for s in symptoms if s in p.core_symptoms]
            matched_assoc = [s for s in symptoms if s in p.associated_symptoms]
            matched_pulse = [s for s in pulses if s in p.pulses]
            missing_core = [s for s in p.core_symptoms if s not in symptoms]
            core_w = sum(p.core_scores.get(s, 0.5) for s in matched_core)
            score = core_w + 0.3 * len(matched_assoc) + 0.4 * len(matched_pulse)
            score -= 0.15 * len(missing_core)
            if matched_core or matched_assoc or matched_pulse:
                results.append(
                    MatchResult(
                        formula=formula,
                        score=round(score, 4),
                        matched_core=matched_core,
                        matched_associated=matched_assoc,
                        matched_pulses=matched_pulse,
                        missing_core=missing_core,
                        supporting_clauses=p.supporting_clauses,
                    )
                )
        results.sort(key=lambda r: -r.score)
        return results[:top_k]
