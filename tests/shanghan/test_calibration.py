"""金标准校准工具链(评审第九条): precision/recall/F1 + 等级分布。"""
import pytest

from evals.calibration.shanghan_gold import evaluate, load_gold, rule_key
from shanghan.runtime import default_result


@pytest.fixture(scope="module")
def report():
    return evaluate(default_result())


def test_all_released_rules_labeled(report):
    for lvl in ("gold", "silver", "bronze"):
        assert report["levels"][lvl]["unlabeled"] == 0


def test_seed_recall_is_one(report):
    # 种子标注以放行集为底,recall 应为 1.0(医师重标后该值才有外部意义)
    assert report["recall_strict"] == 1.0


def test_gold_level_strict_precision(report):
    # 工程种子下,gold 级不应包含 partial/incorrect
    assert report["levels"]["gold"]["precision_strict"] == 1.0


def test_partial_labels_land_in_silver(report):
    assert report["levels"]["silver"]["partial"] > 0


def test_score_bands_monotonic(report):
    g, s = report["levels"]["gold"], report["levels"]["silver"]
    if g["score_min"] is not None and s["score_max"] is not None:
        assert g["score_min"] > s["score_max"]


def test_disclaimer_present(report):
    assert "PENDING_PHYSICIAN_REVIEW" in report["disclaimer"]


def test_gold_file_labels_valid():
    gold = load_gold()
    assert len(gold) >= 39
    assert set(gold.values()) <= {"correct", "partial", "incorrect"}


def test_rule_key_stable():
    result = default_result()
    keys = [rule_key(ar.rule) for ar in result.approved]
    assert len(keys) == len(set(keys))
