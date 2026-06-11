"""端到端窄切片: 文本输入→状态更新→红旗扫描→选问→医生端输出,全程事件入库、审计入链。"""
import pytest
from pydantic import ValidationError

from eventstore import EventStore
from hermes_contracts import ConsentFlags, EscalationLevel, Phase
from hermes_loop.engine import LoopEngine
from hermes_loop.skill_loader import load_skill


def make_engine(encounter_id="it_enc", skill_name="emergency_triage", **kw):
    eng = LoopEngine(encounter_id, skill=load_skill(skill_name),
                     store=EventStore(), **kw)
    eng.begin(ConsentFlags(recording=True, retention=True))
    return eng


class TestBenignFlow:
    def test_full_flow_to_doctor_review(self):
        eng = make_engine()
        res = eng.step("咳嗽两天,流鼻涕")
        assert res.actions[0]["type"] == "question"
        for answer in ["前天开始的", "三分吧", "没有过敏", "好像没有别的"]:
            res = eng.step(answer)
        res = eng.step("在吃感冒冲剂")
        assert eng.state.phase == Phase.DOCTOR_REVIEW
        assert res.actions[-1]["type"] == "awaiting_doctor"
        assert eng.state.question_count == 5
        # 每个症状均可溯源(硬规则5)
        for symptom in eng.state.symptoms:
            assert symptom.sources

    def test_doctor_review_completes_with_summary(self):
        eng = make_engine()
        eng.step("咳嗽两天")
        for answer in ["前天", "三分", "没有过敏", "没有", "没有"]:
            eng.step(answer)
        review = eng.doctor_review(decision="adopt", doctor_id="dr_007")
        assert not review["withheld"]
        assert review["summary"]["channel"] == "patient"
        assert eng.state.phase == Phase.END
        actions = [e["action"] for e in eng.ledger.entries("it_enc")]
        assert "doctor_action" in actions

    def test_doctor_review_wrong_phase_raises(self):
        eng = make_engine()
        with pytest.raises(RuntimeError):
            eng.doctor_review()

    def test_doctor_review_invalid_decision(self):
        eng = make_engine()
        eng.step("咳嗽")
        for answer in ["前天", "三分", "没有过敏", "没有", "没有"]:
            eng.step(answer)
        with pytest.raises(ValueError):
            eng.doctor_review(decision="approve")


class TestEscalation:
    def test_hard_escalation_blocks_further_steps(self):
        eng = make_engine()
        res = eng.step("胸口正中压着痛,出冷汗")
        action = next(a for a in res.actions if a["type"] == "escalation")
        assert action["level"] == "E1"
        assert eng.state.phase == Phase.ESCALATION
        assert eng.state.escalation == EscalationLevel.E1_ER_NOW
        with pytest.raises(RuntimeError, match="升级"):
            eng.step("其实我感觉好多了")

    def test_escalation_audited(self):
        eng = make_engine()
        eng.step("叫不醒了")
        assert any(
            e["action"] == "escalation" for e in eng.ledger.entries("it_enc")
        )
        assert eng.ledger.verify_chain("it_enc")

    def test_soft_escalation_flow_continues(self):
        eng = make_engine()
        res = eng.step("发高烧,脖子硬得动不了")
        assert eng.state.escalation == EscalationLevel.E2_SAME_DAY
        assert eng.state.phase == Phase.HPI
        assert any(a["type"] == "question" for a in res.actions)

    def test_step_after_end_raises(self):
        eng = make_engine()
        eng.step("咳嗽")
        for answer in ["前天", "三分", "没有过敏", "没有", "没有"]:
            eng.step(answer)
        eng.doctor_review()
        with pytest.raises(RuntimeError, match="结束"):
            eng.step("再问一句")


class TestMustNotMissClosure:
    def test_triad_denial_excludes(self):
        eng = make_engine(skill_name="oncology_bone_metastasis")
        eng.step("腰背痛两周了")
        eng.step("没有麻")
        eng.step("没有问题")
        eng.step("没有")
        item = eng.state.differential.must_not_miss[0]
        assert item.status == "excluded"
        assert item.discriminators_pending == []

    def test_probably_denied_keeps_blocking(self):
        eng = make_engine(skill_name="oncology_bone_metastasis")
        eng.step("腰背痛两周了")
        eng.step("好像没有吧")
        item = eng.state.differential.must_not_miss[0]
        assert item.status == "not_excluded"
        assert "saddle_anesthesia" in item.discriminators_pending

    def test_invariant_b_direct_violation_rejected(self):
        eng = make_engine(skill_name="oncology_bone_metastasis")
        eng.step("腰背痛两周了")
        with pytest.raises(ValidationError, match="INVARIANT_B"):
            eng.state.phase = Phase.SUMMARY

    def test_withheld_summary_when_open(self):
        eng = make_engine(skill_name="oncology_bone_metastasis")
        eng.step("腰背痛一个月")
        eng.step("好像没有吧")
        eng.step("没有")
        eng.step("没有")
        while eng.state.phase == Phase.HPI:
            eng.step("没有")
        assert eng.state.phase == Phase.DOCTOR_REVIEW
        review = eng.doctor_review()
        assert review["withheld"]
        assert "spinal_cord_compression" in review["unexcluded"]


class TestEventSourcing:
    def test_resume_rebuilds_identical_state(self):
        store = EventStore()
        eng = LoopEngine("resume_enc", skill=load_skill("emergency_triage"),
                         store=store)
        eng.begin(ConsentFlags(recording=True, retention=True))
        eng.step("咳嗽两天,有点发烧")
        eng.step("前天开始的")

        resumed = LoopEngine.resume(
            "resume_enc", skill=load_skill("emergency_triage"), store=store
        )
        assert resumed.state.model_dump() == eng.state.model_dump()
        assert resumed.texts == eng.texts
        assert resumed._awaiting_slot == eng._awaiting_slot

    def test_resume_can_continue_conversation(self):
        store = EventStore()
        eng = LoopEngine("resume_enc2", skill=load_skill("emergency_triage"),
                         store=store)
        eng.begin(ConsentFlags(recording=True, retention=True))
        eng.step("咳嗽两天")
        resumed = LoopEngine.resume(
            "resume_enc2", skill=load_skill("emergency_triage"), store=store
        )
        res = resumed.step("前天开始的")
        assert resumed.state.answered_slots.get("onset") == "present"
        assert any(a["type"] == "question" for a in res.actions)

    def test_resume_preserves_escalation_lock(self):
        store = EventStore()
        eng = LoopEngine("resume_enc3", skill=load_skill("emergency_triage"),
                         store=store)
        eng.begin(ConsentFlags(recording=True, retention=True))
        eng.step("突然剧烈头痛")
        resumed = LoopEngine.resume(
            "resume_enc3", skill=load_skill("emergency_triage"), store=store
        )
        assert resumed.state.phase == Phase.ESCALATION
        with pytest.raises(RuntimeError):
            resumed.step("我好点了")


class TestDegradationIntegration:
    def test_llm_failures_degrade_but_redflag_survives(self):
        def broken_llm(text: str):
            raise TimeoutError("llm down")

        eng = make_engine(encounter_id="deg_enc", llm=broken_llm)
        eng.step("咳嗽两天")
        eng.step("前天开始")
        eng.step("三分")
        assert eng.degradation.level == "L1"
        assert eng.state.degraded_mode == "L1"
        # 降级层红旗照常工作
        res = eng.step("胸口正中压着痛,出冷汗")
        assert any(a["type"] == "escalation" for a in res.actions)

    def test_healthy_llm_stays_l0(self):
        eng = make_engine(encounter_id="deg_enc2", llm=lambda t: "ok")
        eng.step("咳嗽两天")
        eng.step("前天开始")
        eng.step("三分")
        assert eng.degradation.level == "L0"


class TestDialectClarification:
    def test_ambiguous_term_clarified_once(self):
        eng = make_engine(encounter_id="dia_enc")
        res = eng.step("咳了三天,心口有点痛")
        clarify = next(a for a in res.actions if a["type"] == "clarify")
        assert set(clarify["options"]) == {"上腹部", "胸部"}
        # 同一术语不重复澄清
        res2 = eng.step("心口还是不舒服")
        assert not any(a["type"] == "clarify" for a in res2.actions)

    def test_unambiguous_term_extracted(self):
        eng = make_engine(encounter_id="dia_enc2")
        eng.step("这两天有点气紧")
        assert any(s.concept_id == "呼吸困难" for s in eng.state.symptoms)
