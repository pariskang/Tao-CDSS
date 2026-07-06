"""Split-conformal 弃权机制: 覆盖保证/小样本 fail-safe/预测集行为。"""
import pytest

from shanghan.conformal import ConformalCalibrator, calibrate_from_patterns


class TestThreshold:
    def test_small_calibration_always_abstains(self):
        """n < (1-α)/α 时无有限样本保证,必须永远弃权(fail-safe)。"""
        cal = ConformalCalibrator(alpha=0.1).fit([1.0] * 5)  # 需 n≥9
        assert cal.threshold() is None
        d = cal.decide({"桂枝汤": 99.0})
        assert d.abstain and "校准集不足" in d.reason

    def test_quantile_rank_formula(self):
        # n=9, α=0.1: rank = ceil(10*0.9) = 9 → 取最大不合规分数
        scores = [float(i) for i in range(1, 10)]  # 1..9
        cal = ConformalCalibrator(alpha=0.1).fit(scores)
        assert cal.threshold() == -1.0  # 最大的 -score

    def test_empirical_coverage_on_calibration_like_data(self):
        """经验覆盖率 ≥ 1-α: 用与校准同分布的伪测试点验证。"""
        scores = [1.0 + 0.1 * i for i in range(20)]
        cal = ConformalCalibrator(alpha=0.2).fit(scores)
        q = cal.threshold()
        covered = sum(1 for s in scores if -s <= q)
        assert covered / len(scores) >= 0.8


class TestDecide:
    def _cal(self):
        return ConformalCalibrator(alpha=0.1, max_set_size=2).fit(
            [2.0 + 0.05 * i for i in range(20)]
        )

    def test_confident_match_accepted(self):
        d = self._cal().decide({"麻黄汤": 3.0, "无关方": 0.1})
        assert not d.abstain
        assert d.prediction_set == ["麻黄汤"]

    def test_all_below_threshold_abstains(self):
        d = self._cal().decide({"甲": 0.5, "乙": 0.3})
        assert d.abstain and "置信不足" in d.reason

    def test_oversized_set_abstains(self):
        d = self._cal().decide({"甲": 3.0, "乙": 3.0, "丙": 3.0})
        assert d.abstain and "预测集过大" in d.reason

    def test_empty_candidates_abstains(self):
        assert self._cal().decide({}).abstain


class TestPatternSelfCalibration:
    def test_calibrates_from_real_patterns(self):
        from shanghan.runtime import default_result

        cal = calibrate_from_patterns(default_result().patterns)
        assert cal.calibration_n >= 10  # 23 个方证 pattern 提供留一校准点
        assert cal.threshold() is not None

    def test_match_handler_reports_conformal(self):
        from shanghan.runtime import default_rag

        out = default_rag().ask("头痛发热,身疼腰痛,无汗而喘,应该匹配什么方证?")
        conf = out["conformal"]
        assert conf["coverage_target"] == 0.9
        assert conf["calibration_n"] >= 10
        assert isinstance(conf["abstain"], bool)
        if not conf["abstain"]:
            assert "麻黄汤" in conf["prediction_set"]

    def test_gibberish_symptoms_abstain(self):
        from shanghan.runtime import default_rag

        out = default_rag().ask("匹配什么方证? 与伤寒无关的表现")
        assert out["conformal"]["abstain"]


class TestNormalization:
    def test_softmax_normalizes_unbounded_scores(self):
        from shanghan.conformal import normalize_scores

        norm = normalize_scores({"甲": 100.0, "乙": 99.0, "丙": -5.0})
        assert abs(sum(norm.values()) - 1.0) < 1e-9
        assert all(0.0 < v < 1.0 for v in norm.values())
        assert norm["甲"] > norm["乙"] > norm["丙"]

    def test_normalization_order_preserving(self):
        """归一化保序: 预测集成员随原始分数单调。"""
        from shanghan.conformal import normalize_scores

        raw = {"a": 3.0, "b": 1.0, "c": 0.5}
        norm = normalize_scores(raw)
        assert sorted(raw, key=raw.get) == sorted(norm, key=norm.get)

    def test_empty_scores(self):
        from shanghan.conformal import normalize_scores

        assert normalize_scores({}) == {}


class TestLOOCoverage:
    def test_loo_coverage_near_target(self):
        """留一经验覆盖率应落在目标 1-α 附近(Wilson CI 内含目标或更高)。"""
        from shanghan.conformal import loo_coverage
        from shanghan.runtime import default_result

        report = loo_coverage(default_result().patterns, alpha=0.1)
        assert report["coverage"] is not None
        assert report["evaluable_folds"] >= 10
        # 自校准分布上覆盖率不得显著低于目标: CI 上界须达标
        assert report["ci95"][1] >= report["coverage_target"]

    def test_too_few_points_honest_none(self):
        from shanghan.conformal import loo_coverage

        report = loo_coverage({}, alpha=0.1)
        assert report["coverage"] is None
