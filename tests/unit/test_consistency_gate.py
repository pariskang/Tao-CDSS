"""字段级一致性门控: 分档判决/medoid/性质测试(纯函数,零 LLM)。"""
import pytest
from pydantic import BaseModel

from hermes_llm.consistency_gate import DEGRADE, FAIL, PASS, GateResult, gate


class Verdict(BaseModel):
    verdict: str
    problems: list[str] = []
    note: str = ""


def V(v, probs=(), note=""):
    return Verdict(verdict=v, problems=list(probs), note=note)


class TestDecisions:
    def test_unanimous_passes(self):
        r = gate([V("pass"), V("pass"), V("pass")])
        assert r.decision == PASS and r.min_agreement == 1.0

    def test_standard_field_below_quorum_degrades(self):
        r = gate([V("pass"), V("warn"), V("fail")])
        assert r.decision == DEGRADE and r.chosen_index is not None

    def test_safety_field_any_disagreement_fails(self):
        r = gate([V("pass"), V("pass"), V("warn")],
                 safety_fields=("verdict",))
        assert r.decision == FAIL and r.chosen_index is None

    def test_info_field_never_blocks(self):
        r = gate([V("pass", note="a"), V("pass", note="b"),
                  V("pass", note="c")], info_fields=("note", "problems"))
        assert r.decision == PASS

    def test_empty_samples_fail(self):
        assert gate([]).decision == FAIL

    def test_single_sample_passes(self):
        assert gate([V("pass")]).decision == PASS


class TestMedoid:
    def test_medoid_is_real_sample_not_stitched(self):
        """返回的必须是真实样本下标,不逐字段拼装缝合怪。"""
        samples = [V("pass", ["x"]), V("pass", []), V("warn", [])]
        r = gate(samples)
        assert r.chosen_index in (0, 1, 2)
        chosen = samples[r.chosen_index]
        # medoid 与众数字段吻合度最高: verdict=pass(2票),problems众数二选一
        assert chosen.verdict == "pass"

    def test_medoid_deterministic_tie(self):
        samples = [V("pass"), V("pass")]
        assert gate(samples).chosen_index == gate(samples).chosen_index == 0


class TestProperties:
    def test_permutation_invariant_decision(self):
        a = [V("pass"), V("pass"), V("warn")]
        b = [V("warn"), V("pass"), V("pass")]
        assert gate(a).decision == gate(b).decision
        assert gate(a).min_agreement == gate(b).min_agreement

    def test_adding_agreeing_sample_never_lowers_agreement(self):
        base = [V("pass"), V("pass"), V("warn")]
        more = base + [V("pass")]
        assert gate(more).min_agreement >= gate(base).min_agreement

    def test_adding_disagreeing_sample_never_raises_agreement(self):
        base = [V("pass"), V("pass"), V("pass")]
        more = base + [V("fail")]
        assert gate(more).min_agreement <= gate(base).min_agreement

    def test_entropy_exact_values(self):
        r = gate([V("pass"), V("pass"), V("warn"), V("warn")])
        assert r.field_report["verdict"]["entropy_bits"] == 1.0  # 2/2 均分
        r2 = gate([V("pass")] * 4)
        assert r2.field_report["verdict"]["entropy_bits"] == 0.0

    def test_pass_result_validates_against_schema(self):
        r = gate([V("pass"), V("pass")])
        samples = [V("pass"), V("pass")]
        assert Verdict.model_validate(samples[r.chosen_index].model_dump())
