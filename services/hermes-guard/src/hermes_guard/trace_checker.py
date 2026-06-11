"""溯源检查器(协议 L8.4):症状级结论必须可溯源至 utterance/EHR。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TraceViolation:
    kind: str
    detail: str


def check_state(
    state,
    known_utterance_ids: set[str],
    known_ehr_refs: set[str] | None = None,
) -> list[TraceViolation]:
    ehr_refs = known_ehr_refs or set()
    violations: list[TraceViolation] = []
    for symptom in state.symptoms:
        for src in symptom.sources:
            if src.kind == "utterance" and src.ref_id not in known_utterance_ids:
                violations.append(
                    TraceViolation(
                        kind="dangling_utterance_ref",
                        detail=f"{symptom.concept_id} -> {src.ref_id}",
                    )
                )
            elif src.kind == "ehr" and src.ref_id not in ehr_refs:
                violations.append(
                    TraceViolation(
                        kind="dangling_ehr_ref",
                        detail=f"{symptom.concept_id} -> {src.ref_id}",
                    )
                )
    return violations


def check_claims(claims) -> list[TraceViolation]:
    """无证据绑定的非科普主张视为违规(不得平铺呈现)。"""
    violations: list[TraceViolation] = []
    for claim in claims:
        if claim.claim_type != "education" and not claim.evidence_bindings:
            violations.append(
                TraceViolation(kind="unbound_claim", detail=claim.claim_id)
            )
    return violations
