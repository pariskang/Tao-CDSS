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
