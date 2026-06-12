import pytest
from pydantic import ValidationError

from hermes_contracts import (
    MAX_QUESTIONS,
    CDSSClaim,
    ClinicalState,
    Differential,
    DifferentialItem,
    Phase,
    SourceRef,
    Symptom,
    SymptomStatus,
    ToolCallEnvelope,
)
from hermes_contracts.export_json_schema import MODELS, export


class TestSourceRef:
    def test_valid_kinds(self):
        for kind in ("utterance", "ehr", "doctor_input", "tool"):
            assert SourceRef(kind=kind, ref_id="x").kind == kind

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            SourceRef(kind="hallucination", ref_id="x")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            SourceRef(kind="utterance", ref_id="x", asr_confidence=1.5)


class TestSymptom:
    def test_requires_at_least_one_source(self):
        """硬规则5: 无源写入被契约拒绝。"""
        with pytest.raises(ValidationError):
            Symptom(concept_id="咳嗽", raw_text="咳", status=SymptomStatus.PRESENT,
                    sources=[])


class TestInvariantB:
    def _blocking_state(self, question_count: int) -> ClinicalState:
        state = ClinicalState(
            encounter_id="t",
            differential=Differential(
                must_not_miss=[DifferentialItem(condition="spinal_cord_compression")]
            ),
        )
        state.question_count = question_count
        return state

    def test_summary_blocked_when_open_and_under_cap(self):
        state = self._blocking_state(question_count=3)
        with pytest.raises(ValidationError, match="INVARIANT_B"):
            state.phase = Phase.SUMMARY

    def test_summary_allowed_at_question_cap(self):
        state = self._blocking_state(question_count=MAX_QUESTIONS)
        state.phase = Phase.SUMMARY
        assert state.phase == Phase.SUMMARY

    def test_summary_allowed_when_excluded(self):
        state = ClinicalState(
            encounter_id="t",
            differential=Differential(
                must_not_miss=[
                    DifferentialItem(condition="x", status="excluded")
                ]
            ),
        )
        state.phase = Phase.SUMMARY
        assert state.phase == Phase.SUMMARY

    def test_failed_assignment_preserves_state(self):
        state = self._blocking_state(question_count=0)
        with pytest.raises(ValidationError):
            state.phase = Phase.SUMMARY
        assert state.phase == Phase.CONSENT


class TestDifferential:
    def test_blocking_items(self):
        diff = Differential(
            must_not_miss=[
                DifferentialItem(condition="a"),
                DifferentialItem(condition="b", status="excluded"),
            ]
        )
        assert [i.condition for i in diff.blocking_items()] == ["a"]

    def test_status_pattern(self):
        with pytest.raises(ValidationError):
            DifferentialItem(condition="a", status="maybe")


class TestClaimAndEnvelope:
    def test_claim_class_bounds(self):
        with pytest.raises(ValidationError):
            CDSSClaim(claim_id="c", text="t", claim_type="risk", output_class=6)

    def test_claim_type_pattern(self):
        with pytest.raises(ValidationError):
            CDSSClaim(claim_id="c", text="t", claim_type="diagnosis", output_class=3)

    def test_envelope_defaults(self):
        env = ToolCallEnvelope(
            tool_call_id="tc1", encounter_id="e1", agent="intake",
            skill="emergency_triage", tool="calculator.nrs_pain",
        )
        assert env.idempotency_key is None
        assert env.input == {}


class TestSchemaExport:
    def test_export_writes_all_models(self, tmp_path):
        written = export(tmp_path)
        assert len(written) == len(MODELS)
        for p in written:
            assert p.exists()
            assert p.read_text(encoding="utf-8").startswith("{")
