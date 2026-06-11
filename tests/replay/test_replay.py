"""SP 回放(协议 L9 层次一):红旗召回率必须 100%。"""
import pytest

from evals.simulation.replay import run_skill


@pytest.mark.parametrize("skill", ["emergency_triage", "oncology_bone_metastasis"])
def test_all_cases_pass(skill):
    report = run_skill(skill)
    assert report["passed"] == report["total"], report["failed"]


@pytest.mark.parametrize("skill", ["emergency_triage", "oncology_bone_metastasis"])
def test_red_flag_recall_100(skill):
    report = run_skill(skill)
    assert report["red_flag_recall"] == 1.0
