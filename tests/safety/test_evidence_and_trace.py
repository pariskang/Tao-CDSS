"""协议 L8.4: Claim-Evidence Binding 与溯源检查。"""
import pytest

from hermes_contracts import (
    CDSSClaim,
    ClinicalState,
    SourceRef,
    Symptom,
    SymptomStatus,
)
from hermes_guard.evidence_checker import check_claim, default_nli, triage_claims
from hermes_guard.trace_checker import check_claims, check_state


def make_claim(text="建议完善胸部CT", bindings=None, claim_type="investigation"):
    return CDSSClaim(
        claim_id="c1",
        text=text,
        claim_type=claim_type,
        output_class=3,
        evidence_bindings=bindings or [],
    )


class TestDefaultNLI:
    def test_empty_evidence_neutral(self):
        assert default_nli("建议X", "") == "neutral"

    def test_contradiction_detected(self):
        assert (
            default_nli("推荐使用阿司匹林", "指南明确不推荐使用阿司匹林用于该人群")
            == "contradicted"
        )

    def test_entailed_on_overlap(self):
        assert (
            default_nli("建议完善胸部CT检查", "对该类患者应建议完善胸部CT检查以排除")
            == "entailed"
        )

    def test_neutral_on_low_overlap(self):
        assert default_nli("建议完善胸部CT检查", "高血压患者应低盐饮食") == "neutral"

    def test_single_char_claim_neutral(self):
        assert default_nli("x", "任何证据文本") == "neutral"


class TestCheckClaim:
    def test_no_bindings_neutral(self):
        assert check_claim(make_claim(), {}) == "neutral"

    def test_contradicted_wins(self):
        claim = make_claim(
            text="推荐使用阿司匹林",
            bindings=[{"evidence_id": "e1"}, {"evidence_id": "e2"}],
        )
        evidence = {"e1": "推荐使用阿司匹林预防", "e2": "不推荐使用阿司匹林"}
        assert check_claim(claim, evidence) == "contradicted"

    def test_entailed(self):
        claim = make_claim(bindings=[{"evidence_id": "e1"}])
        assert (
            check_claim(claim, {"e1": "应建议完善胸部CT以明确诊断"}) == "entailed"
        )

    def test_missing_evidence_id_neutral(self):
        claim = make_claim(bindings=[{"evidence_id": "missing"}])
        assert check_claim(claim, {}) == "neutral"

    def test_binding_without_id_key(self):
        claim = make_claim(bindings=[{}])
        assert check_claim(claim, {}) == "neutral"


class TestTriageClaims:
    def test_buckets(self):
        entailed = make_claim(bindings=[{"evidence_id": "e1"}])
        folded = make_claim()
        folded.claim_id = "c2"
        blocked = make_claim(text="推荐使用阿司匹林", bindings=[{"evidence_id": "e2"}])
        blocked.claim_id = "c3"
        evidence = {
            "e1": "应建议完善胸部CT以明确诊断",
            "e2": "不推荐使用阿司匹林",
        }
        out = triage_claims([entailed, folded, blocked], evidence)
        assert [c.claim_id for c in out["presented"]] == ["c1"]
        assert [c.claim_id for c in out["folded"]] == ["c2"]
        assert [c.claim_id for c in out["blocked"]] == ["c3"]
        assert entailed.verifier_nli == "entailed"
        assert folded.verifier_nli == "neutral"
        assert blocked.verifier_nli == "contradicted"


class TestTraceChecker:
    def _state(self, kind="utterance", ref="utt_000"):
        return ClinicalState(
            encounter_id="t",
            symptoms=[
                Symptom(
                    concept_id="咳嗽",
                    raw_text="咳了三天",
                    status=SymptomStatus.PRESENT,
                    sources=[SourceRef(kind=kind, ref_id=ref)],
                )
            ],
        )

    def test_valid_utterance_ref(self):
        assert check_state(self._state(), {"utt_000"}) == []

    def test_dangling_utterance_ref(self):
        violations = check_state(self._state(ref="utt_999"), {"utt_000"})
        assert violations[0].kind == "dangling_utterance_ref"

    def test_valid_ehr_ref(self):
        state = self._state(kind="ehr", ref="obs_1")
        assert check_state(state, set(), {"obs_1"}) == []

    def test_dangling_ehr_ref(self):
        state = self._state(kind="ehr", ref="obs_9")
        violations = check_state(state, set(), {"obs_1"})
        assert violations[0].kind == "dangling_ehr_ref"

    def test_doctor_input_source_passes(self):
        state = self._state(kind="doctor_input", ref="dr_1")
        assert check_state(state, set()) == []

    def test_unbound_claim_violation(self):
        violations = check_claims([make_claim()])
        assert violations[0].kind == "unbound_claim"

    def test_education_claim_exempt(self):
        claim = make_claim(text="注意休息", claim_type="education")
        assert check_claims([claim]) == []

    def test_bound_claim_passes(self):
        assert check_claims([make_claim(bindings=[{"evidence_id": "e1"}])]) == []
