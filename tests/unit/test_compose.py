from hermes_contracts import (
    CDSSClaim,
    ClinicalState,
    Differential,
    DifferentialItem,
    SourceRef,
    Symptom,
    SymptomStatus,
)
from hermes_compose.composer import compose_doctor_output, compose_patient_summary


def make_state(**kw) -> ClinicalState:
    defaults = dict(
        encounter_id="t",
        chief_complaints=["腰背痛两周"],
        symptoms=[
            Symptom(
                concept_id="腰背痛", raw_text="腰背痛两周",
                status=SymptomStatus.PRESENT,
                sources=[SourceRef(kind="utterance", ref_id="utt_000")],
            ),
            Symptom(
                concept_id="鞍区麻木", raw_text="没有麻",
                status=SymptomStatus.DENIED,
                sources=[SourceRef(kind="utterance", ref_id="utt_001")],
            ),
        ],
    )
    defaults.update(kw)
    return ClinicalState(**defaults)


class TestPatientSummary:
    def test_summary_contains_no_differential(self):
        state = make_state(
            differential=Differential(
                must_not_miss=[
                    DifferentialItem(condition="spinal_cord_compression",
                                     probability=0.06)
                ]
            )
        )
        summary = compose_patient_summary(state)
        assert "spinal_cord_compression" not in summary["text"]
        assert "0.06" not in summary["text"]

    def test_summary_echoed_dose_redacted(self):
        state = make_state(chief_complaints=["医生让我吃布洛芬400mg"])
        summary = compose_patient_summary(state)
        assert "400mg" not in summary["text"]

    def test_summary_structure(self):
        summary = compose_patient_summary(make_state())
        assert summary["channel"] == "patient"
        assert "腰背痛" in summary["text"]
        assert "医生面诊为准" in summary["text"]


class TestDoctorOutput:
    def _claims(self):
        return [
            CDSSClaim(claim_id="c1", text="建议完善胸椎MRI检查",
                      claim_type="investigation", output_class=3,
                      evidence_bindings=[{"evidence_id": "e1"}]),
            CDSSClaim(claim_id="c2", text="可能与骨转移进展相关",
                      claim_type="risk", output_class=3),
            CDSSClaim(claim_id="c3", text="推荐使用阿司匹林",
                      claim_type="medication", output_class=4,
                      evidence_bindings=[{"evidence_id": "e2"}]),
        ]

    def _evidence(self):
        return {
            "e1": "出现神经症状时应建议完善胸椎MRI检查以排除脊髓压迫",
            "e2": "该人群不推荐使用阿司匹林",
        }

    def test_unexcluded_flagged(self):
        state = make_state(
            differential=Differential(
                must_not_miss=[DifferentialItem(condition="spinal_cord_compression")]
            )
        )
        out = compose_doctor_output(state)
        assert out["unexcluded"] == ["spinal_cord_compression"]
        assert out["soap"]["assessment"]["must_not_miss"][0]["flag"] == "未排除"

    def test_claims_triaged(self):
        out = compose_doctor_output(make_state(), self._claims(), self._evidence())
        assert [c["claim_id"] for c in out["claims"]] == ["c1"]
        assert [c["claim_id"] for c in out["speculation"]] == ["c2"]
        assert out["blocked_claims"] == ["c3"]

    def test_claim_dose_redacted_without_fill(self):
        claim = CDSSClaim(
            claim_id="c9", text="可给予唑来膦酸4mg静滴",
            claim_type="medication", output_class=4,
            evidence_bindings=[{"evidence_id": "e9"}],
        )
        out = compose_doctor_output(
            make_state(), [claim], {"e9": "可给予唑来膦酸静滴治疗骨转移"}
        )
        rendered = (out["claims"] + out["speculation"])[0]
        assert "4mg" not in rendered["text"]
        assert rendered["dose_violations"] == ["4mg"]

    def test_sources_visible_to_doctor(self):
        out = compose_doctor_output(make_state())
        symptom = out["soap"]["subjective"]["symptoms"][0]
        assert symptom["sources"][0]["ref_id"] == "utt_000"
