"""LLM 后端与治理网关: 结构化输出/重试修复/剂量出站/审计/成本。"""
import pytest

from audit_chain import AuditLedger
from hermes_llm import (
    LLMGateway,
    PromptRegistry,
    StubLLMBackend,
    build_system_prompt,
)
from hermes_llm.gateway import LLMCallFailed
from hermes_llm.structured import (
    SCHEMAS,
    ParseFailure,
    ReviewVerdictModel,
    extract_json,
    parse_structured,
)


class TestPromptRegistry:
    def test_default_roles_registered(self):
        roles = PromptRegistry.roles()
        for role in ("intake_extractor", "tcm_reviewer", "critic", "judge"):
            assert role in roles

    def test_build_system_prompt_is_static(self):
        """硬规则4: system 段只来自静态模板,两次调用完全一致。"""
        assert build_system_prompt("critic") == build_system_prompt("critic")
        assert "不可信数据" in build_system_prompt("critic")

    def test_unknown_role_raises(self):
        with pytest.raises(KeyError):
            build_system_prompt("nonexistent_role")


class TestStructuredParser:
    def test_extract_json_from_noise(self):
        data = extract_json('前置文本 {"verdict": "pass", "problems": []} 后置')
        assert data["verdict"] == "pass"

    def test_no_json_raises(self):
        with pytest.raises(ParseFailure):
            extract_json("没有任何JSON")

    def test_schema_validation(self):
        model = parse_structured('{"verdict": "fail", "problems": ["x"]}',
                                 ReviewVerdictModel)
        assert model.verdict == "fail"

    def test_bad_schema_raises(self):
        with pytest.raises(ParseFailure):
            parse_structured('{"verdict": 123}', ReviewVerdictModel)


class TestGateway:
    def test_structured_call_success(self):
        backend = StubLLMBackend(responses=['{"verdict": "pass", "problems": []}'])
        gw = LLMGateway(backend, ledger=AuditLedger(), encounter_id="t1")
        out = gw.structured_call("critic", {"claim": "x"}, SCHEMAS["ReviewVerdictModel"])
        assert out.verdict == "pass"
        # 不可信数据只进 user 段(硬规则4)
        system = backend.calls[0][0]
        assert system["role"] == "system"
        assert "claim" not in system["content"]

    def test_retry_repair_loop(self):
        backend = StubLLMBackend(
            responses=["这不是JSON", '{"verdict": "warn", "problems": []}']
        )
        gw = LLMGateway(backend, ledger=AuditLedger(), encounter_id="t2")
        out = gw.structured_call("critic", {}, SCHEMAS["ReviewVerdictModel"])
        assert out.verdict == "warn"
        assert len(backend.calls) == 2
        # 第二次调用带修复指示
        assert "无法解析" in backend.calls[1][-1]["content"]

    def test_exhausted_retries_raise(self):
        backend = StubLLMBackend(default="始终不是JSON")
        gw = LLMGateway(backend, ledger=AuditLedger(), encounter_id="t3",
                        max_retries=1)
        with pytest.raises(LLMCallFailed):
            gw.structured_call("critic", {}, SCHEMAS["ReviewVerdictModel"])

    def test_llm_dose_output_redacted_and_flagged(self):
        """硬规则1: LLM 输出路径上的剂量被出站扫描置换并审计 CRIT。"""
        backend = StubLLMBackend(
            responses=['{"bullets": ["建议布洛芬200mg口服"]}']
        )
        ledger = AuditLedger()
        gw = LLMGateway(backend, ledger=ledger, encounter_id="t4")
        out = gw.structured_call("summarizer", {}, SCHEMAS["SummaryModel"])
        assert "200mg" not in out.bullets[0]
        assert "[剂量须医生确认]" in out.bullets[0]
        entry = ledger.entries("t4")[-1]
        assert entry["payload"]["data"]["severity"] == "CRIT"
        assert entry["payload"]["data"]["dose_violations"] == ["200mg"]

    def test_audit_and_cost_log(self):
        backend = StubLLMBackend()
        ledger = AuditLedger()
        gw = LLMGateway(backend, ledger=ledger, encounter_id="t5")
        gw.structured_call("critic", {"x": 1}, SCHEMAS["ReviewVerdictModel"])
        entries = ledger.entries("t5")
        assert entries[0]["action"] == "llm_call"
        assert entries[0]["actor"] == "llm:critic"
        assert ledger.verify_chain("t5")
        summary = gw.cost_log.summary()
        assert summary["calls"] == 1
        assert summary["by_role"]["critic"]["calls"] == 1
        assert summary["cost_unknown_calls"] == 0


class TestLiteLLMBackend:
    def test_importable_and_configurable(self, monkeypatch):
        from hermes_llm.backend import LiteLLMBackend

        monkeypatch.setenv("HERMES_LLM_MODEL", "openai/gpt-4o-mini")
        backend = LiteLLMBackend()
        assert backend.model == "openai/gpt-4o-mini"

    def test_missing_model_raises(self, monkeypatch):
        from hermes_llm.backend import BackendUnavailable, LiteLLMBackend

        monkeypatch.delenv("HERMES_LLM_MODEL", raising=False)
        with pytest.raises(BackendUnavailable):
            LiteLLMBackend()
