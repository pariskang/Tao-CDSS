"""硬规则2: 红旗引擎纯规则、零 LLM/网络依赖;E0/E1 升级不可降级。"""
import inspect
from pathlib import Path

import pytest

import redflag_engine.engine as rf_module
from hermes_contracts.paths import repo_root
from redflag_engine import SEVERITY, RedFlagEngine

GLOBAL_RULES = repo_root() / "knowledge" / "red_flags" / "global.yaml"


@pytest.fixture()
def engine() -> RedFlagEngine:
    return RedFlagEngine.from_yaml(GLOBAL_RULES)


class TestPurity:
    def test_no_llm_or_network_imports(self):
        src = inspect.getsource(rf_module)
        forbidden = ("requests", "httpx", "openai", "anthropic", "llm",
                     "socket", "urllib")
        import_lines = [
            line for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            for word in forbidden:
                assert word not in line, f"红旗引擎禁止依赖 {word}: {line}"


class TestRuleValidation:
    def test_missing_id_rejected(self):
        with pytest.raises(ValueError, match="id/level"):
            RedFlagEngine([{"level": "E0", "any_of": ["x"]}])

    def test_missing_level_rejected(self):
        with pytest.raises(ValueError, match="id/level"):
            RedFlagEngine([{"id": "x", "any_of": ["x"]}])

    def test_bad_level_rejected(self):
        with pytest.raises(ValueError, match="非法升级等级"):
            RedFlagEngine([{"id": "x", "level": "E9", "any_of": ["x"]}])

    def test_missing_condition_rejected(self):
        with pytest.raises(ValueError, match="any_of/all_of"):
            RedFlagEngine([{"id": "x", "level": "E0"}])


class TestGlobalRules:
    """每条关键红旗规则 ≥3 正例 2 反例。"""

    @pytest.mark.parametrize(
        "text",
        ["他突然意识丧失了", "怎么都叫不醒", "已经昏迷了", "好像没有呼吸了"],
    )
    def test_e0_loss_of_consciousness_positive(self, engine, text):
        result = engine.scan_text(text)
        assert result.level == "E0"
        assert "loss_of_consciousness" in result.hit_ids()

    @pytest.mark.parametrize(
        "text", ["精神不太好但叫得醒", "晚上睡得沉", "有点犯困"]
    )
    def test_e0_loss_of_consciousness_negative(self, engine, text):
        assert "loss_of_consciousness" not in engine.scan_text(text).hit_ids()

    @pytest.mark.parametrize(
        "text",
        [
            "胸口正中压着痛,还出冷汗",
            "胸口闷痛,左臂也麻",
            "压榨样疼痛,大汗淋漓",
        ],
    )
    def test_e1_chest_pain_positive(self, engine, text):
        result = engine.scan_text(text)
        assert result.level == "E1"
        assert "chest_pain_pressing_diaphoresis" in result.hit_ids()

    @pytest.mark.parametrize(
        "text",
        ["胸口痛,但是没有出汗,也不放射", "咳嗽厉害,嗓子痛", "胸口痛,没有冷汗"],
    )
    def test_e1_chest_pain_negative(self, engine, text):
        assert "chest_pain_pressing_diaphoresis" not in engine.scan_text(text).hit_ids()

    @pytest.mark.parametrize(
        "text",
        ["突然剧烈头痛", "像炸开一样的头痛", "这是我一生中最痛的头痛"],
    )
    def test_e1_thunderclap_positive(self, engine, text):
        assert "thunderclap_headache" in engine.scan_text(text).hit_ids()

    @pytest.mark.parametrize("text", ["头有点胀", "偏头痛老毛病了"])
    def test_e1_thunderclap_negative(self, engine, text):
        assert "thunderclap_headache" not in engine.scan_text(text).hit_ids()

    @pytest.mark.parametrize(
        "text",
        ["嘴角歪了", "说话突然不清楚", "半边麻木使不上劲"],
    )
    def test_e1_stroke_positive(self, engine, text):
        assert "fast_positive_stroke" in engine.scan_text(text).hit_ids()

    @pytest.mark.parametrize("text", ["笑起来很自然", "口齿清楚"])
    def test_e1_stroke_negative(self, engine, text):
        assert "fast_positive_stroke" not in engine.scan_text(text).hit_ids()


class TestNegationHandling:
    def test_multichar_negation(self, engine):
        assert "loss_of_consciousness" not in engine.scan_text("没有昏迷").hit_ids()

    def test_singlechar_negation_adjacent(self, engine):
        result = engine.scan_text("胸口痛,不出汗,无放射")
        assert "chest_pain_pressing_diaphoresis" not in result.hit_ids()

    def test_singlechar_negation_not_adjacent_still_positive(self, engine):
        # "走不动路还出汗": "不"否定的是"动",不应否定"出汗"(召回优先)
        result = engine.scan_text("胸口压着痛,走不动路还出汗")
        assert "chest_pain_pressing_diaphoresis" in result.hit_ids()

    def test_second_occurrence_positive(self, engine):
        result = engine.scan_text("之前没有出汗,现在胸口压着痛而且出汗了")
        assert "chest_pain_pressing_diaphoresis" in result.hit_ids()


class TestSeverityAndAccumulation:
    def test_highest_severity_wins(self, engine):
        result = engine.scan_text("叫不醒,而且突然剧烈头痛")
        assert result.level == "E0"
        assert len(result.hits) >= 2

    def test_scan_texts_accumulates_across_turns(self, engine):
        result = engine.scan_texts(["胸口正中压着痛", "还出冷汗"])
        assert result.level == "E1"

    def test_no_hits_level_none(self, engine):
        result = engine.scan_text("最近胃口不错")
        assert result.level is None
        assert result.hits == ()

    def test_severity_table(self):
        assert SEVERITY["E0"] < SEVERITY["E1"] < SEVERITY["E4"]

    def test_all_of_partial_no_match(self, engine):
        # 妊娠大出血: 仅有"怀孕"无出血组 → 不触发
        assert "pregnancy_heavy_bleeding" not in engine.scan_text("我怀孕了").hit_ids()

    def test_e2_rules(self, engine):
        result = engine.scan_text("发高烧,脖子硬得不敢动")
        assert result.level == "E2"

    def test_from_yaml_multiple_files(self, tmp_path):
        extra = tmp_path / "extra.yaml"
        extra.write_text(
            "rules:\n  - id: t1\n    level: E2\n    any_of: [测试词]\n",
            encoding="utf-8",
        )
        eng = RedFlagEngine.from_yaml(GLOBAL_RULES, extra)
        assert "t1" in eng.scan_text("测试词").hit_ids()

    def test_from_yaml_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        eng = RedFlagEngine.from_yaml(empty)
        assert eng.scan_text("任何文本").level is None
