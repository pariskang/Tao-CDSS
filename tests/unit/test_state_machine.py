import pytest

from hermes_contracts import ClinicalState, EscalationLevel, Phase
from hermes_loop.state_machine import TransitionError, set_escalation, transition


def state_at(phase: Phase, escalation: EscalationLevel | None = None) -> ClinicalState:
    s = ClinicalState(encounter_id="t")
    s.phase = phase if phase == Phase.CONSENT else s.phase
    # 绕过流程直接设定相(测试用)
    object.__setattr__  # noqa: B018
    s.__dict__["phase"] = phase
    if escalation:
        s.__dict__["escalation"] = escalation
    return s


class TestLegalTransitions:
    @pytest.mark.parametrize(
        "src,dst",
        [
            (Phase.CONSENT, Phase.IDENTITY_PROXY),
            (Phase.IDENTITY_PROXY, Phase.CHIEF_COMPLAINT),
            (Phase.CHIEF_COMPLAINT, Phase.RED_FLAG),
            (Phase.RED_FLAG, Phase.HPI),
            (Phase.HPI, Phase.PMH_MED),
            (Phase.DIFFERENTIAL, Phase.HPI),
            (Phase.DIFFERENTIAL, Phase.EVIDENCE),
            (Phase.VERIFY, Phase.DOCTOR_REVIEW),
        ],
    )
    def test_allowed(self, src, dst):
        s = state_at(src)
        transition(s, dst)
        assert s.phase == dst

    def test_same_phase_noop(self):
        s = state_at(Phase.HPI)
        transition(s, Phase.HPI)
        assert s.phase == Phase.HPI

    @pytest.mark.parametrize(
        "src,dst",
        [
            (Phase.CONSENT, Phase.SUMMARY),
            (Phase.CHIEF_COMPLAINT, Phase.DOCTOR_REVIEW),
            (Phase.SUMMARY, Phase.HPI),
        ],
    )
    def test_illegal_rejected(self, src, dst):
        with pytest.raises(TransitionError, match="非法相变"):
            transition(state_at(src), dst)


class TestEscalationInvariantA:
    def test_escalation_reachable_from_any_active_phase(self):
        for phase in (Phase.CHIEF_COMPLAINT, Phase.HPI, Phase.PMH_MED, Phase.VERIFY):
            s = state_at(phase)
            transition(s, Phase.ESCALATION)
            assert s.phase == Phase.ESCALATION

    def test_escalation_not_from_end(self):
        with pytest.raises(TransitionError):
            transition(state_at(Phase.END), Phase.ESCALATION)

    def test_e1_cannot_auto_deescalate(self):
        s = state_at(Phase.ESCALATION, EscalationLevel.E1_ER_NOW)
        with pytest.raises(TransitionError, match="人工"):
            transition(s, Phase.HPI, actor="system")

    def test_e1_human_can_deescalate(self):
        s = state_at(Phase.ESCALATION, EscalationLevel.E1_ER_NOW)
        transition(s, Phase.HPI, actor="human")
        assert s.phase == Phase.HPI

    def test_escalation_to_end_allowed(self):
        s = state_at(Phase.ESCALATION, EscalationLevel.E0_CALL_120)
        transition(s, Phase.END)
        assert s.phase == Phase.END

    def test_soft_escalation_phase_locked_for_system(self):
        s = state_at(Phase.ESCALATION, EscalationLevel.E2_SAME_DAY)
        with pytest.raises(TransitionError):
            transition(s, Phase.HPI, actor="system")


class TestSetEscalation:
    def test_upgrade_always_allowed(self):
        s = state_at(Phase.HPI, EscalationLevel.E2_SAME_DAY)
        set_escalation(s, EscalationLevel.E0_CALL_120)
        assert s.escalation == EscalationLevel.E0_CALL_120

    def test_e0_system_downgrade_rejected(self):
        s = state_at(Phase.HPI, EscalationLevel.E0_CALL_120)
        with pytest.raises(TransitionError, match="人工"):
            set_escalation(s, EscalationLevel.E3_WITHIN_48H, actor="system")

    def test_e0_human_downgrade_allowed(self):
        s = state_at(Phase.HPI, EscalationLevel.E0_CALL_120)
        set_escalation(s, EscalationLevel.E3_WITHIN_48H, actor="human")
        assert s.escalation == EscalationLevel.E3_WITHIN_48H

    def test_soft_level_can_move(self):
        s = state_at(Phase.HPI, EscalationLevel.E3_WITHIN_48H)
        set_escalation(s, EscalationLevel.E2_SAME_DAY)
        assert s.escalation == EscalationLevel.E2_SAME_DAY


class TestPsychRisk:
    def test_enter_from_anywhere(self):
        for phase in (Phase.CONSENT, Phase.HPI, Phase.DOCTOR_REVIEW):
            s = state_at(phase)
            transition(s, Phase.PSYCH_RISK)
            assert s.phase == Phase.PSYCH_RISK

    def test_exit_requires_human(self):
        s = state_at(Phase.PSYCH_RISK)
        with pytest.raises(TransitionError):
            transition(s, Phase.HPI, actor="system")
        transition(s, Phase.END, actor="human")
        assert s.phase == Phase.END
