import pytest

from audit_chain import AuditLedger
from hermes_loop.degradation import DegradationController


def test_consecutive_failures_degrade_stepwise():
    ctl = DegradationController(failure_threshold=3)
    for _ in range(2):
        ctl.record_llm_failure()
    assert ctl.level == "L0"
    ctl.record_llm_failure()
    assert ctl.level == "L1"
    for _ in range(3):
        ctl.record_llm_failure()
    assert ctl.level == "L2"
    # L2 已是最低层,不再下降
    for _ in range(3):
        ctl.record_llm_failure()
    assert ctl.level == "L2"


def test_success_resets_counter():
    ctl = DegradationController(failure_threshold=3)
    ctl.record_llm_failure()
    ctl.record_llm_failure()
    ctl.record_llm_success()
    ctl.record_llm_failure()
    ctl.record_llm_failure()
    assert ctl.level == "L0"


def test_force_degrade_and_validation():
    ctl = DegradationController()
    ctl.force_degrade("L2", "维护窗口")
    assert ctl.level == "L2"
    with pytest.raises(ValueError, match="升档需人工"):
        ctl.force_degrade("L1", "x")
    with pytest.raises(ValueError, match="非法"):
        ctl.force_degrade("L9", "x")


def test_restore_requires_human():
    ctl = DegradationController()
    ctl.force_degrade("L1", "x")
    with pytest.raises(PermissionError):
        ctl.restore("L0", actor="system")
    ctl.restore("L0", actor="human")
    assert ctl.level == "L0"
    with pytest.raises(ValueError, match="非法"):
        ctl.restore("L9", actor="human")


def test_degradation_audited():
    ledger = AuditLedger()
    ctl = DegradationController(ledger=ledger, encounter_id="e1",
                                failure_threshold=1)
    ctl.record_llm_failure()
    entries = ledger.entries("e1")
    assert entries[0]["action"] == "degradation"
    assert entries[0]["payload"]["data"]["level"] == "L1"
