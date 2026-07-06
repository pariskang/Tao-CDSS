from hermes_contracts import (
    MAX_QUESTIONS,
    ClinicalState,
    Differential,
    DifferentialItem,
)
from hermes_loop.question_planner import Slot, next_question

SLOTS = [
    Slot(name="rf", question="红旗问题?", priority=5, red_flag=True),
    Slot(name="disc", question="判别问题?", priority=50),
    Slot(name="req", question="必填问题?", priority=10, required=True),
    Slot(name="opt", question="可选问题?", priority=60),
    Slot(name="sens", question="敏感问题?", priority=70, sensitive=True,
         reason="为了更好地帮助您,"),
]


def make_state(**kw) -> ClinicalState:
    s = ClinicalState(encounter_id="t", **kw)
    return s


def test_red_flag_first():
    q = next_question(make_state(), SLOTS)
    assert q.slot == "rf"
    assert q.rationale == "red_flag"


def test_must_not_miss_discriminator_second():
    state = make_state(
        differential=Differential(
            must_not_miss=[
                DifferentialItem(condition="scc", discriminators_pending=["disc"])
            ]
        ),
        answered_slots={"rf": "denied"},
    )
    q = next_question(state, SLOTS)
    assert q.slot == "disc"
    assert q.rationale == "must_not_miss"


def test_information_gain_with_lr_table():
    state = make_state(
        differential=Differential(
            most_likely=[DifferentialItem(condition="pneumonia", probability=0.5)]
        ),
        answered_slots={"rf": "denied"},
    )
    lr_table = {("opt", "pneumonia"): 8.0}
    q = next_question(state, SLOTS, lr_table)
    assert q.slot == "opt"
    assert q.rationale == "information_gain"


def test_required_before_optional():
    state = make_state(answered_slots={"rf": "denied"})
    q = next_question(state, SLOTS)
    assert q.slot == "req"
    assert q.rationale == "template_required"


def test_template_fallback_and_sensitive_reason():
    state = make_state(
        answered_slots={"rf": "denied", "req": "present", "opt": "present",
                        "disc": "denied"}
    )
    q = next_question(state, SLOTS)
    assert q.slot == "sens"
    assert q.text.startswith("为了更好地帮助您,")


def test_no_reask_answered_or_asked():
    state = make_state(
        asked_slots=["rf", "req"],
        answered_slots={"disc": "denied", "opt": "present", "sens": "present"},
    )
    assert next_question(state, SLOTS) is None


def test_question_cap():
    state = make_state()
    state.question_count = MAX_QUESTIONS
    assert next_question(state, SLOTS) is None


def test_zero_lr_ignored():
    state = make_state(
        differential=Differential(
            most_likely=[DifferentialItem(condition="x", probability=0.5)]
        ),
        answered_slots={"rf": "denied"},
    )
    q = next_question(state, SLOTS, {("opt", "x"): 0})
    assert q.rationale == "template_required"


def test_skill_lr_table_activates_info_gain():
    """skill 声明 lr_table 后信息增益分支被真实激活(此前是死参数)。"""
    state = make_state(
        differential=Differential(
            most_likely=[DifferentialItem(condition="x", probability=0.5)]
        ),
        answered_slots={"rf": "denied"},
    )
    q = next_question(state, SLOTS, {("opt", "x"): 3.0})
    assert q.slot == "opt"
    assert q.rationale == "information_gain"


def test_lr_table_bad_slot_rejected_at_load():
    import pytest
    import yaml
    from hermes_loop.skill_loader import load_skill

    skill = load_skill("oncology_bone_metastasis")
    slots_file = skill.path / "slots.yaml"
    data = yaml.safe_load(slots_file.read_text(encoding="utf-8"))
    data["lr_table"] = [{"slot": "ghost_slot", "condition": "x", "lr": 2.0}]
    import tempfile, shutil, pathlib
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        target = root / "skills" / "bad_skill"
        shutil.copytree(skill.path, target)
        (target / "slots.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="lr_table 引用未定义槽位"):
            load_skill("bad_skill", root=root)


class TestBayesianEIG:
    """预期信息增益选问(两点模型互信息,AMIE/MAI-DxO 准则的确定性简化)。"""

    def test_uninformative_lr_gains_zero(self):
        from hermes_loop.question_planner import _expected_information_gain
        s = Slot(name="opt", question="q")
        assert _expected_information_gain(s, {"x": 0.5}, {("opt", "x"): 1.0}) == 0.0

    def test_gain_monotone_in_lr(self):
        from hermes_loop.question_planner import _expected_information_gain
        s = Slot(name="opt", question="q")
        g = [_expected_information_gain(s, {"x": 0.3}, {("opt", "x"): lr})
             for lr in (1.5, 3.0, 10.0)]
        assert g[0] < g[1] < g[2]
        assert all(x > 0 for x in g)

    def test_posterior_updates_from_answers(self):
        from hermes_loop.question_planner import posterior_probabilities
        state = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.2)]
            ),
            answered_slots={"opt": "present"},
        )
        post = posterior_probabilities(state, {("opt", "x"): 4.0})
        # p=0.2, L=4: 后验 = 0.2*4/(0.2*4+0.8) = 0.5
        assert abs(post["x"] - 0.5) < 1e-9
        state2 = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.2)]
            ),
            answered_slots={"opt": "denied"},
        )
        post2 = posterior_probabilities(state2, {("opt", "x"): 4.0})
        # denied: p/(p+(1-p)L) = 0.2/(0.2+3.2) ≈ 0.0588
        assert post2["x"] < 0.2

    def test_unknown_answer_does_not_update(self):
        from hermes_loop.question_planner import posterior_probabilities
        state = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.2)]
            ),
            answered_slots={"opt": "unknown"},
        )
        assert posterior_probabilities(state, {("opt", "x"): 4.0})["x"] == 0.2

    def test_answered_slot_gain_reflects_posterior(self):
        """答完高 LR 判别问题后,同一条件的剩余问题增益下降(不确定性已削减)。"""
        from hermes_loop.question_planner import _info_gain
        s2 = Slot(name="opt2", question="q2")
        lr = {("opt", "x"): 9.0, ("opt2", "x"): 3.0}
        before = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.5)]
            ),
        )
        after = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.5)]
            ),
            answered_slots={"opt": "denied"},
        )
        assert _info_gain(s2, after, lr) < _info_gain(s2, before, lr)


class TestCategoricalEIG:
    """联合类别互信息(BED-LLM 2508.21184): 修正逐条二值 MI 的重复计分。"""

    def test_weak_broad_slot_no_longer_beats_sharp_slot(self):
        """"弱触多病"槽位不得再靠重复计分压过"锐分主病"槽位。"""
        from hermes_loop.question_planner import _expected_information_gain
        weak = Slot(name="weak", question="q")
        sharp = Slot(name="sharp", question="q")
        post = {f"d{i}": 0.15 for i in range(5)}
        lr = {("weak", f"d{i}"): 1.3 for i in range(5)}
        lr[("sharp", "d0")] = 9.0
        g_weak = _expected_information_gain(weak, post, lr)
        g_sharp = _expected_information_gain(sharp, post, lr)
        assert g_sharp > g_weak

    def test_eig_bounded_by_prior_entropy(self):
        from hermes_loop.question_planner import (
            _categorical_entropy, _categorical_prior,
            _expected_information_gain,
        )
        s = Slot(name="opt", question="q")
        post = {"x": 0.4, "y": 0.3}
        g = _expected_information_gain(s, post, {("opt", "x"): 8.0})
        assert 0.0 < g <= _categorical_entropy(_categorical_prior(post))

    def test_all_lr_one_gain_zero(self):
        from hermes_loop.question_planner import _expected_information_gain
        s = Slot(name="opt", question="q")
        assert _expected_information_gain(
            s, {"x": 0.3, "y": 0.3}, {("opt", "x"): 1.0, ("opt", "y"): 1.0}
        ) == 0.0


class TestCostAwareSelection:
    """EIG/负担比 + 停问准则(MAI-DxO Stewardship/ACTMED/Calibrate-Then-Act)。"""

    def _diff(self, p=0.3):
        return Differential(
            most_likely=[DifferentialItem(condition="x", probability=p)]
        )

    def test_cheap_slot_beats_expensive_at_cost_ratio(self):
        cheap = Slot(name="cheap", question="q1", cost=1.0)
        costly = Slot(name="costly", question="q2", cost=4.0)
        state = make_state(differential=self._diff(),
                           answered_slots={"rf": "denied"})
        slots = [Slot(name="rf", question="rf", red_flag=True),
                 cheap, costly]
        lr = {("cheap", "x"): 4.0, ("costly", "x"): 8.0}
        q = next_question(state, slots, lr)
        assert q.slot == "cheap" and q.rationale == "information_gain"

    def test_confidence_tau_stops_info_gain(self):
        """后验达 τ 后信息增益分支停问,让位模板必填。"""
        opt = Slot(name="opt", question="q", cost=1.0)
        req = Slot(name="req", question="qr", required=True)
        state = make_state(differential=self._diff(p=0.97),
                           answered_slots={"rf": "denied"})
        q = next_question(state, [Slot(name="rf", question="r", red_flag=True),
                                  opt, req], {("opt", "x"): 4.0})
        assert q.slot == "req" and q.rationale == "template_required"

    def test_eig_eps_floor_skips_marginal_questions(self):
        opt = Slot(name="opt", question="q")
        req = Slot(name="req", question="qr", required=True)
        state = make_state(differential=self._diff(p=0.001),
                           answered_slots={"rf": "denied"})
        q = next_question(state, [opt, req], {("opt", "x"): 1.01})
        assert q.rationale == "template_required"

    def test_red_flag_immune_to_cost_and_tau(self):
        """红旗槽位在任何置信/预算下都先问(安全分支不受停问影响)。"""
        rf = Slot(name="rf", question="r", red_flag=True, cost=100.0)
        state = make_state(differential=self._diff(p=0.99))
        q = next_question(state, [rf, Slot(name="opt", question="q")],
                          {("opt", "x"): 9.0})
        assert q.slot == "rf" and q.rationale == "red_flag"

    def test_balance_tiebreak_deterministic(self):
        """EIG/cost 并列时平衡切分度决胜,结果确定。"""
        a = Slot(name="a", question="qa")
        b = Slot(name="b", question="qb")
        state = make_state(
            differential=Differential(
                most_likely=[DifferentialItem(condition="x", probability=0.5),
                             DifferentialItem(condition="y", probability=0.5)]
            ),
            answered_slots={"rf": "denied"},
        )
        lr = {("a", "x"): 4.0, ("b", "x"): 4.0}
        q1 = next_question(state, [a, b], lr)
        q2 = next_question(state, [a, b], lr)
        assert q1.slot == q2.slot
