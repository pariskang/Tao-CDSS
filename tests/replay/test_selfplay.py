"""self-play 仿真回归门禁: 两个 skill 的场景全过 + 确定性复现。"""
from evals.simulation.selfplay import run_selfplay


def test_oncology_selfplay_all_pass():
    report = run_selfplay("oncology_bone_metastasis")
    assert report["failed"] == [], report["failed"]
    assert report["passed"] == report["total"]


def test_emergency_selfplay_all_pass():
    report = run_selfplay("emergency_triage")
    assert report["failed"] == [], report["failed"]


def test_selfplay_deterministic():
    r1 = run_selfplay("emergency_triage")
    r2 = run_selfplay("emergency_triage")
    assert r1 == r2
