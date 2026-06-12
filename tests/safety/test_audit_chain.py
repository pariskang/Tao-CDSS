"""硬规则3: 审计哈希链 append-only;篡改任意一行 → verify_chain 必失败。"""
import pytest

from audit_chain import GENESIS, AuditLedger, canonical, row_hash


@pytest.fixture()
def ledger() -> AuditLedger:
    led = AuditLedger()
    led.append("enc1", "system", "encounter_started", {"consent": True})
    led.append("enc1", "agent_intake", "tool_call", {"tool": "triage.red_flag_check"})
    led.append("enc1", "doctor_001", "doctor_action", {"decision": "adopt"})
    led.append("enc2", "system", "encounter_started", {"consent": False})
    return led


class TestHashing:
    def test_row_hash_deterministic(self):
        payload = {"b": 2, "a": 1}
        assert row_hash(GENESIS, payload) == row_hash(GENESIS, {"a": 1, "b": 2})

    def test_canonical_sorted_compact(self):
        assert canonical({"b": 2, "a": 1}) == '{"a":1,"b":2}'

    def test_canonical_preserves_unicode(self):
        assert "胸痛" in canonical({"symptom": "胸痛"})

    def test_genesis_is_zero64(self):
        assert GENESIS == "0" * 64


class TestAppendOnly:
    def test_no_update_or_delete_api(self):
        public = [m for m in dir(AuditLedger) if not m.startswith("_")]
        for name in public:
            assert "update" not in name.lower()
            assert "delete" not in name.lower()

    def test_append_returns_chain_fields(self, ledger):
        row = ledger.append("enc1", "system", "x", {})
        assert row["prev_hash"] != GENESIS
        assert len(row["row_hash"]) == 64

    def test_chains_isolated_per_encounter(self, ledger):
        entries1 = ledger.entries("enc1")
        entries2 = ledger.entries("enc2")
        assert entries1[0]["prev_hash"] == GENESIS
        assert entries2[0]["prev_hash"] == GENESIS

    def test_entries_all(self, ledger):
        assert len(ledger.entries()) == 4


class TestVerification:
    def test_valid_chain_verifies(self, ledger):
        assert ledger.verify_chain("enc1")
        assert ledger.verify_chain("enc2")

    def test_empty_chain_verifies(self, ledger):
        assert ledger.verify_chain("nonexistent")

    @pytest.mark.parametrize("seq_to_tamper", [1, 2, 3])
    def test_tamper_payload_any_row_detected(self, ledger, seq_to_tamper):
        ledger._conn.execute(
            "UPDATE audit_ledger SET payload = ? WHERE seq = ?",
            ('{"actor":"hacker","action":"x","data":{},"encounter_id":"enc1"}',
             seq_to_tamper),
        )
        ledger._conn.commit()
        assert not ledger.verify_chain("enc1")

    def test_tamper_row_hash_detected(self, ledger):
        ledger._conn.execute(
            "UPDATE audit_ledger SET row_hash = ? WHERE seq = 2", ("f" * 64,)
        )
        ledger._conn.commit()
        assert not ledger.verify_chain("enc1")

    def test_tamper_prev_hash_detected(self, ledger):
        ledger._conn.execute(
            "UPDATE audit_ledger SET prev_hash = ? WHERE seq = 3", ("e" * 64,)
        )
        ledger._conn.commit()
        assert not ledger.verify_chain("enc1")

    def test_delete_row_detected(self, ledger):
        ledger._conn.execute("DELETE FROM audit_ledger WHERE seq = 2")
        ledger._conn.commit()
        assert not ledger.verify_chain("enc1")


class TestExport:
    def test_export_case_trace(self, ledger):
        trace = ledger.export_case_trace("enc1")
        assert trace["chain_valid"]
        assert len(trace["entries"]) == 3
        assert trace["encounter_id"] == "enc1"

    def test_export_after_tamper_flags_invalid(self, ledger):
        ledger._conn.execute(
            "UPDATE audit_ledger SET payload = '{}' WHERE seq = 1"
        )
        ledger._conn.commit()
        assert not ledger.export_case_trace("enc1")["chain_valid"]
