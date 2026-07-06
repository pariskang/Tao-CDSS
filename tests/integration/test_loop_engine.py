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

    def test_double_begin_rejected(self):
        eng = make_engine(encounter_id="dbl_enc")
        with pytest.raises(RuntimeError, match="重复 begin"):
            eng.begin(ConsentFlags(recording=True, retention=True))

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

    def test_resume_restores_degradation_level(self):
        def broken_llm(text: str):
            raise TimeoutError("llm down")

        store = EventStore()
        eng = LoopEngine("deg_resume", skill=load_skill("emergency_triage"),
                         store=store, llm=broken_llm)
        eng.begin(ConsentFlags(recording=True, retention=True))
        for answer in ["咳嗽两天", "前天开始", "三分"]:
            eng.step(answer)
        assert eng.degradation.level == "L1"

        resumed = LoopEngine.resume(
            "deg_resume", skill=load_skill("emergency_triage"), store=store
        )
        assert resumed.degradation.level == "L1"
        assert resumed.state.degraded_mode == "L1"

    def test_healthy_llm_stays_l0(self):
        eng = make_engine(encounter_id="deg_enc2", llm=lambda t: "ok")
        eng.step("咳嗽两天")
        eng.step("前天开始")
        eng.step("三分")
        assert eng.degradation.level == "L0"

    def test_l2_reachable_from_l1(self):
        """L1 时 LLM 仍受限调用(mode=extract_only),持续失败可自动到 L2;
        此前 L1 后 LLM 永不再被调用,L2 是不可达死档。"""
        modes = []

        def broken_llm(text, mode=None):
            modes.append(mode)
            raise TimeoutError("llm down")

        eng = make_engine(encounter_id="deg_l2", llm=broken_llm)
        for answer in ["咳嗽两天", "前天开始", "三分",
                       "没有过敏", "没有", "还有点鼻塞"]:
            eng.step(answer)
        assert eng.degradation.level == "L2"
        assert eng.state.degraded_mode == "L2"
        assert "extract_only" in modes  # L1 阶段以受限模式调用
        # L2 后 LLM 完全旁路,红旗照常
        calls_at_l2 = len(modes)
        res = eng.step("突然剧烈头痛")
        assert len(modes) == calls_at_l2
        assert any(a["type"] == "escalation" for a in res.actions)


class TestConfidenceGate:
    def test_low_confidence_triggers_reask(self):
        eng = make_engine(encounter_id="conf_enc")
        eng.step("咳嗽两天")  # 引擎提问 onset
        res = eng.step("含糊不清的回答", asr_confidence=0.5)
        assert res.actions[0]["type"] == "reask"
        # 低置信回答不消费槽位,等待复述
        assert eng.state.answered_slots.get("onset") is None
        assert eng._awaiting_slot == "onset"
        # 复述后正常归一
        res2 = eng.step("前天开始的", asr_confidence=0.95)
        assert eng.state.answered_slots.get("onset") == "present"
        assert any(a["type"] == "question" for a in res2.actions)

    def test_red_flag_overrides_low_confidence(self):
        """召回优先: 红旗扫描先于置信度门控。"""
        eng = make_engine(encounter_id="conf_enc2")
        res = eng.step("胸口正中压着痛,出冷汗", asr_confidence=0.5)
        assert any(a["type"] == "escalation" for a in res.actions)

    def test_medium_confidence_accepted(self):
        eng = make_engine(encounter_id="conf_enc3")
        res = eng.step("咳嗽两天", asr_confidence=0.7)
        assert any(a["type"] == "question" for a in res.actions)


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

    def test_dialect_expansion_feeds_redflag(self):
        """方言候选概念回喂红旗扫描: "心口疼"以[上腹痛,胸痛]参与匹配,
        伴冷汗时按疑似ACS升级,不再因方言表述漏判。"""
        eng = make_engine(encounter_id="dia_rf")
        res = eng.step("心口疼得厉害,还出冷汗")
        assert eng.state.phase == Phase.ESCALATION
        esc = next(a for a in res.actions if a["type"] == "escalation")
        assert esc["level"] == "E1"
        assert "chest_pain_pressing_diaphoresis" in esc["hits"]

    def test_negated_dialect_not_expanded(self):
        """被否定的方言词不扩展: "没有心口疼"不得触发假升级。"""
        eng = make_engine(encounter_id="dia_rf_neg")
        eng.step("没有心口疼,就是有点咳嗽,也没出汗")
        assert eng.state.phase != Phase.ESCALATION
        assert eng.state.escalation is None


class TestClosedLoops:
    def test_hard_escalation_notifies_human(self):
        """E1 话术承诺"已通知分诊台",系统必须真发 notify_human 并留痕。"""
        eng = make_engine(encounter_id="notif_enc")
        res = eng.step("突然剧烈头痛,炸开一样")
        notify = next(a for a in res.actions if a["type"] == "notify_human")
        assert notify["reason"] == "red_flag_E1"
        assert any(
            e["action"] == "notify_human"
            for e in eng.ledger.entries("notif_enc")
        )

    def test_clarify_answer_maps_back_to_term(self):
        """澄清闭环: 患者选择候选后记录为可溯源症状,不再丢失。"""
        eng = make_engine(encounter_id="clar_enc")
        res = eng.step("咳了三天,心口有点痛")
        assert any(a["type"] == "clarify" for a in res.actions)
        eng.step("是胸部这边不舒服")
        assert any(s.concept_id == "胸部" for s in eng.state.symptoms)

    def test_confusion_drug_double_confirm(self):
        """近音药名双确认: 命中混淆对追加 confirm 动作,同药不重复确认。"""
        eng = make_engine(encounter_id="conf_enc")
        res = eng.step("最近在吃阿糖腺苷")
        confirm = next(a for a in res.actions if a["type"] == "confirm")
        assert confirm["term"] == "阿糖腺苷"
        assert "阿糖胞苷" in confirm["alternatives"]
        res2 = eng.step("对,就是阿糖腺苷")
        assert not any(a["type"] == "confirm" for a in res2.actions)

    def test_numeric_readback(self):
        """数字回读: 数值型回答追加 readback 确认,不阻断流程。"""
        eng = make_engine(encounter_id="rb_enc")
        eng.step("咳嗽两天")
        res = eng.step("38.5度,昨天开始的")
        rb = next(a for a in res.actions if a["type"] == "readback")
        assert "38.5" in rb["values"]
        assert any(a["type"] == "question" for a in res.actions)

    def test_low_confidence_denial_cannot_exclude(self):
        """accept_uncertain(0.65-0.85): 低置信否认不得排除危重鉴别。"""
        eng = make_engine(encounter_id="lc_enc",
                          skill_name="oncology_bone_metastasis")
        eng.step("腰背痛两周了")
        eng.step("没有麻", asr_confidence=0.7)  # 低置信否认
        item = eng.state.differential.must_not_miss[0]
        assert "saddle_anesthesia" in item.discriminators_pending
        assert item.status == "not_excluded"


class TestAnswerPolarity:
    def test_mixed_polarity_answer_not_misread_as_denial(self):
        """"会阴麻木得厉害,但大便没有问题"按词窗判极性:
        明确肯定的判别症状不得被全文中的"没有"误判为否认。"""
        eng = make_engine(encounter_id="pol_enc",
                          skill_name="oncology_bone_metastasis")
        eng.step("腰背痛两周了")  # 首问 saddle_anesthesia
        eng.step("会阴那里麻木得厉害,但是大便没有问题")
        item = eng.state.differential.must_not_miss[0]
        assert item.status == "not_excluded"
        assert "saddle_anesthesia" not in item.discriminators_pending or \
            eng.state.answered_slots.get("saddle_anesthesia") == "present"
        assert eng.state.answered_slots["saddle_anesthesia"] == "present"


class TestDoctorAdjudication:
    def _to_review(self):
        eng = make_engine(encounter_id="adj_enc",
                          skill_name="oncology_bone_metastasis")
        eng.step("腰背痛一个月")
        eng.step("好像没有吧")  # probably_denied → 保持 blocking
        eng.step("没有")
        eng.step("没有")
        while eng.state.phase == Phase.HPI:
            eng.step("没有")
        assert eng.state.phase == Phase.DOCTOR_REVIEW
        return eng

    def test_doctor_resolution_breaks_withheld_loop(self):
        eng = self._to_review()
        assert eng.doctor_review()["withheld"]
        review = eng.doctor_review(
            decision="adopt", doctor_id="dr_007",
            resolved_conditions={"spinal_cord_compression": "excluded"},
        )
        assert not review["withheld"]
        assert eng.state.phase == Phase.END
        entry = [e for e in eng.ledger.entries("adj_enc")
                 if e["action"] == "doctor_action"][-1]
        assert entry["payload"]["data"]["resolved_conditions"] == {
            "spinal_cord_compression": "excluded"
        }

    def test_invalid_resolution_rejected(self):
        eng = self._to_review()
        with pytest.raises(ValueError, match="非法 must_not_miss"):
            eng.doctor_review(
                resolved_conditions={"spinal_cord_compression": "cured"}
            )
