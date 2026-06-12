"""硬规则6: 心理危机旁路 — 固定话术/禁入记忆/不可自动退出。"""
import pytest

from eventstore import EventStore
from hermes_contracts import ConsentFlags, Phase
from hermes_guard import psych_monitor
from hermes_loop.engine import LoopEngine
from hermes_loop.skill_loader import load_skill
from hermes_loop.state_machine import TransitionError, transition

RISK_TEXT = "最近总觉得活着没意思,不想活了"


class TestMonitor:
    @pytest.mark.parametrize(
        "text",
        ["我想自杀", "不想活了", "活着没意思", "想割腕", "我会伤害自己",
         "真想跳楼", "我要和他同归于尽"],
    )
    def test_triggered(self, text):
        assert psych_monitor.triggered(text)

    @pytest.mark.parametrize(
        "text", ["心情不太好", "最近压力大", "睡不好觉", "腰痛三天了"]
    )
    def test_not_triggered(self, text):
        assert not psych_monitor.triggered(text)

    def test_recall_first_no_negation_escape(self):
        # 召回优先: 否定句仍触发人工通道
        assert psych_monitor.triggered("我不会自杀的,你放心")

    def test_detect_kind(self):
        assert psych_monitor.detect_kind("不想活了") == "self_harm"
        assert psych_monitor.detect_kind("我要伤害别人") == "harm_others"

    def test_scripts_are_fixed_text(self):
        script = psych_monitor.get_script("self_harm")
        assert script["script_id"] == "psych_self_harm_v1"
        assert script["text"]
        assert script["notify_human"] is True

    def test_unknown_kind_raises(self):
        with pytest.raises(KeyError):
            psych_monitor.get_script("nonexistent_kind")

    def test_custom_path(self, tmp_path):
        p = tmp_path / "scripts.yaml"
        p.write_text(
            "self_harm:\n  script_id: t1\n  text: 固定文案\n  notify_human: true\n",
            encoding="utf-8",
        )
        assert psych_monitor.get_script("self_harm", path=str(p))["script_id"] == "t1"


class TestEngineBypass:
    @pytest.fixture()
    def engine(self) -> LoopEngine:
        eng = LoopEngine("psych_test", skill=load_skill("emergency_triage"),
                         store=EventStore())
        eng.begin(ConsentFlags(recording=True, retention=True))
        return eng

    def test_trigger_enters_psych_risk(self, engine):
        res = engine.step(RISK_TEXT)
        assert engine.state.phase == Phase.PSYCH_RISK
        types = [a["type"] for a in res.actions]
        assert "psych_script" in types
        assert "notify_human" in types

    def test_script_is_fixed_not_generated(self, engine):
        res = engine.step(RISK_TEXT)
        script_action = next(a for a in res.actions if a["type"] == "psych_script")
        assert script_action["text"] == psych_monitor.get_script("self_harm")["text"]

    def test_raw_text_never_enters_eventstore(self, engine):
        engine.step(RISK_TEXT)
        for event in engine._store.events("psych_test"):
            assert RISK_TEXT not in str(event["payload"])
            assert "不想活" not in str(event["payload"])

    def test_raw_text_in_audit_only(self, engine):
        engine.step(RISK_TEXT)
        audit_texts = str(engine.ledger.entries("psych_test"))
        assert "不想活" in audit_texts

    def test_channel_held_until_human(self, engine):
        engine.step(RISK_TEXT)
        res = engine.step("我没事了,继续问诊吧")
        assert engine.state.phase == Phase.PSYCH_RISK
        assert res.actions[0]["type"] == "psych_hold"

    def test_no_automatic_exit(self, engine):
        engine.step(RISK_TEXT)
        with pytest.raises(TransitionError, match="人工"):
            transition(engine.state, Phase.HPI, actor="system")

    def test_human_can_release(self, engine):
        engine.step(RISK_TEXT)
        engine.human_release_psych("staff_001")
        assert engine.state.phase == Phase.END
