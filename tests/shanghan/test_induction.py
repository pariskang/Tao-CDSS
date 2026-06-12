"""归纳层: 特异性加权核心证候 / posthoc 标注 / 鉴别对。"""
import pytest

from shanghan.pipeline import ShanghanPipeline


@pytest.fixture(scope="module")
def result():
    return ShanghanPipeline().run()


class TestFormulaPatterns:
    def test_specific_symptom_is_core(self, result):
        gegen = result.patterns["葛根汤"]
        assert "项背强几几" in gegen.core_symptoms

    def test_nonspecific_symptom_not_core(self, result):
        """发热跨多个方证,特异性低 → 不应成为核心证候(评审十三条)。"""
        for formula in ("桂枝汤", "麻黄汤", "小青龙汤"):
            p = result.patterns[formula]
            assert "发热" not in p.core_symptoms, formula
            assert "发热" in p.associated_symptoms

    def test_core_scores_recorded(self, result):
        p = result.patterns["麻黄汤"]
        assert p.core_scores
        for s in p.core_symptoms:
            assert p.core_scores[s] >= 0.35

    def test_contraindication_attached(self, result):
        assert "酒客病" in result.patterns["桂枝汤"].contraindications

    def test_patterns_marked_posthoc(self, result):
        for p in result.patterns.values():
            assert p.source_level == "posthoc_induction"


class TestSixChannel:
    def test_all_channels_present(self, result):
        assert set(result.channels) == {
            "taiyang", "yangming", "shaoyang", "taiyin", "shaoyin", "jueyin",
        }

    def test_outline_anchored(self, result):
        ty = result.channels["taiyang"]
        assert ty.outline_clause == "SHL_001"
        assert "恶寒" in ty.outline_conditions

    def test_posthoc_label_not_autonomous_discovery(self, result):
        """评审第八条: 六经亚型是后世框架锚定,必须带 posthoc 标注。"""
        for s in result.channels.values():
            assert s.source_level == "posthoc_induction"

    def test_channel_formulas_anchored(self, result):
        assert "小柴胡汤" in result.channels["shaoyang"].formulas
        assert "麻黄细辛附子汤" in result.channels["shaoyin"].formulas


class TestDifferential:
    def test_guizhi_vs_mahuang_discriminators(self, result):
        pair = next(
            p for p in result.differentials
            if {p.formula_a, p.formula_b} == {"桂枝汤", "麻黄汤"}
        )
        a_side = pair.discriminators_a if pair.formula_a == "桂枝汤" else pair.discriminators_b
        b_side = pair.discriminators_b if pair.formula_a == "桂枝汤" else pair.discriminators_a
        assert "汗出" in a_side
        assert "无汗" in b_side
