"""端到端回归: 固定期望计数 + 审计链 + 全量约束。"""
import pytest

from shanghan.pipeline import ShanghanPipeline


@pytest.fixture(scope="module")
def pipeline():
    return ShanghanPipeline()


@pytest.fixture(scope="module")
def result(pipeline):
    return pipeline.run()


def test_regression_counts(result):
    assert result.stats["clauses"] == 26
    assert result.stats["initial_rules"] == 24
    assert result.stats["rejected"] == 0
    assert result.stats["by_type"] == {
        "channel_outline_rule": 6,
        "contraindication_rule": 1,
        "formula_pattern_rule": 15,
        "mistreatment_rule": 2,
    }
    assert result.stats["formula_patterns"] == 14


def test_no_released_formula_rule_without_formula(result):
    """P0 总闸: 放行集中绝不存在无方剂的方证规则。"""
    for ar in result.approved:
        if ar.release_level == "rejected":
            continue
        if ar.rule.rule_type in ("formula_pattern_rule", "mistreatment_rule"):
            assert ar.rule.then_conclusions.get("formula"), ar.rule.rule_id


def test_all_released_rules_have_branch_evidence(result):
    for ar in result.approved:
        if ar.release_level == "rejected":
            continue
        rule = ar.rule
        assert rule.evidence_span
        assert rule.evidence_offsets[1] > rule.evidence_offsets[0]
        assert rule.branch_id
        assert rule.source_level == "clause_branch"


def test_review_audit_chain_valid(pipeline, result):
    assert pipeline.ledger.verify_chain("shanghan_pipeline")
    entries = pipeline.ledger.entries("shanghan_pipeline")
    assert len(entries) == result.stats["initial_rules"]


def test_release_levels_are_engineering_confidence(result):
    """gold/silver/bronze 只是工程置信等级;此处仅验证分层单调性。"""
    scores = {lvl: [] for lvl in ("gold", "silver", "bronze")}
    for ar in result.approved:
        if ar.release_level in scores:
            scores[ar.release_level].append(ar.score)
    if scores["gold"] and scores["silver"]:
        assert min(scores["gold"]) > max(scores["silver"])
