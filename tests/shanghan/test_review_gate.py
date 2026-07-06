"""P0 核心: ReleaseGate 硬拒绝条件 + AutoRepair 不"删到能通过"。"""
import pytest

from shanghan.corpus import corpus_index, load_formula_dict
from shanghan.evidence import EvidenceVerifier
from shanghan.review import (
    AutoRepairAgent,
    ReviewPipeline,
    SemanticReviewer,
    ShanghanCritic,
)
from shanghan.rules import InitialRule, InitialRuleExtractor


@pytest.fixture(scope="module")
def index():
    return corpus_index()


@pytest.fixture(scope="module")
def rules(index):
    return InitialRuleExtractor().extract_corpus(list(index.values()))


@pytest.fixture()
def pipeline(index):
    formula_dict = load_formula_dict()
    return ReviewPipeline(
        verifier=EvidenceVerifier(index),
        semantic=SemanticReviewer(),
        critic=ShanghanCritic(
            {f: m["channel_anchor"] for f, m in formula_dict["formulas"].items()}
        ),
        clause_channels={cid: c.channel for cid, c in index.items()},
    )


def guizhi_rule(rules) -> InitialRule:
    return next(
        r for r in rules
        if r.then_conclusions.get("formula") == "桂枝汤"
        and r.rule_type == "formula_pattern_rule"
    )


class TestReleaseGateHardRejections:
    """P0-1/P0-2: 这些条件必须 rejected,不允许以 bronze/silver 存活。"""

    def test_semantic_fail_is_hard_rejected(self, pipeline, rules):
        rule = guizhi_rule(rules)
        # 把该规则自身的某个条件词列入后世术语黑名单 → semantic fail,
        # 而证据回源与 critic 均可通过 → 必须仍被硬拒绝
        banned = rule.condition_terms()[0]
        verdict = SemanticReviewer(posthoc_terms=[banned]).review(rule)
        assert verdict.verdict == "fail"
        level = ReviewPipeline._release_gate(
            score=0.95, evidence_ok=True, sem="fail", crit="pass", rule=rule
        )
        assert level == "rejected"

    def test_formula_rule_without_formula_rejected(self, rules):
        """无方剂结论的方证规则即使高分也必须 rejected。"""
        rule = guizhi_rule(rules)
        stripped = rule.model_copy(
            update={"then_conclusions": {"strength": "主之"}}
        )
        level = ReviewPipeline._release_gate(
            score=0.99, evidence_ok=True, sem="pass", crit="pass", rule=stripped
        )
        assert level == "rejected"

    def test_empty_rule_rejected(self, rules):
        rule = guizhi_rule(rules)
        empty = rule.model_copy(
            update={"if_conditions": {}, "then_conclusions": {},
                    "rule_type": "channel_outline_rule"}
        )
        level = ReviewPipeline._release_gate(
            score=0.99, evidence_ok=True, sem="pass", crit="pass", rule=empty
        )
        assert level == "rejected"

    def test_evidence_fail_rejected(self, rules):
        level = ReviewPipeline._release_gate(
            score=0.99, evidence_ok=False, sem="pass", crit="pass",
            rule=guizhi_rule(rules),
        )
        assert level == "rejected"

    def test_critic_fail_rejected(self, rules):
        level = ReviewPipeline._release_gate(
            score=0.99, evidence_ok=True, sem="pass", crit="fail",
            rule=guizhi_rule(rules),
        )
        assert level == "rejected"

    def test_low_score_rejected(self, rules):
        level = ReviewPipeline._release_gate(
            score=0.30, evidence_ok=True, sem="pass", crit="pass",
            rule=guizhi_rule(rules),
        )
        assert level == "rejected"

    @pytest.mark.parametrize(
        "score,expected", [(0.95, "gold"), (0.80, "silver"), (0.65, "bronze")]
    )
    def test_thresholds(self, rules, score, expected):
        level = ReviewPipeline._release_gate(
            score=score, evidence_ok=True, sem="pass", crit="pass",
            rule=guizhi_rule(rules),
        )
        assert level == expected


class TestFullPipelineRejection:
    def test_formula_clause_mismatch_must_be_rejected(self, pipeline, rules):
        """评审第二条危险场景: 方剂结论被指向不在 conclusion_span 的方剂
        → 不允许以任何等级存活(不只是'结论被剥离')。"""
        rule = guizhi_rule(rules)
        fake = rule.model_copy(
            update={"then_conclusions": {"formula": "大承气汤", "strength": "主之"}}
        )
        ar = pipeline.review(fake)
        assert ar.release_level == "rejected"

    def test_fabricated_condition_repaired_then_released(self, pipeline, rules):
        """非核心虚构条件 → repair 删除 → 重新回源后可放行。"""
        rule = guizhi_rule(rules)
        conds = dict(rule.if_conditions)
        conds["symptoms"] = list(conds["symptoms"]) + ["虚构症状"]
        fake = rule.model_copy(update={"if_conditions": conds})
        ar = pipeline.review(fake)
        assert ar.release_level != "rejected"
        assert any("虚构症状" in r for r in ar.repairs)
        assert "虚构症状" not in ar.rule.condition_terms()

    def test_repair_emptying_all_conditions_rejected(self, pipeline, rules):
        rule = guizhi_rule(rules)
        fake = rule.model_copy(
            update={"if_conditions": {"symptoms": ["虚构甲", "虚构乙"]}}
        )
        ar = pipeline.review(fake)
        assert ar.release_level == "rejected"

    def test_posthoc_term_stripped_by_repair(self, pipeline, rules):
        """后世术语(表虚证)不可能出现在原文 span → repair 删除后规则以
        干净形态放行,且放行形态中不得残留该术语。"""
        rule = guizhi_rule(rules)
        conds = dict(rule.if_conditions)
        conds["symptoms"] = list(conds["symptoms"]) + ["表虚证"]
        ar = pipeline.review(rule.model_copy(update={"if_conditions": conds}))
        assert "表虚证" not in ar.rule.condition_terms()
        if ar.release_level != "rejected":
            assert ar.semantic != "fail"

    def test_semantic_fail_with_clean_evidence_rejected_e2e(self, pipeline, rules):
        """评审 P0 场景全管线复现: 方剂结论被剥离但 strength 仍在
        → evidence_ok=True, semantic=fail → 必须 rejected,
        不允许以 bronze/silver 存活。"""
        rule = guizhi_rule(rules)
        stripped = rule.model_copy(
            update={"then_conclusions": {"strength": "主之"}}
        )
        ar = pipeline.review(stripped)
        assert ar.evidence_verified  # 证据检查本身是通过的
        assert ar.semantic == "fail"
        assert ar.release_level == "rejected"


class TestAutoRepair:
    def test_repair_only_deletes_never_fabricates(self, rules):
        rule = guizhi_rule(rules)
        conds = dict(rule.if_conditions)
        original_terms = set(rule.condition_terms())
        conds["symptoms"] = list(conds["symptoms"]) + ["虚构症状"]
        fake = rule.model_copy(update={"if_conditions": conds})
        repaired, repairs, must_reject = AutoRepairAgent().repair(fake)
        assert not must_reject
        assert set(repaired.condition_terms()) <= original_terms

    def test_removing_formula_flags_must_reject(self, rules):
        rule = guizhi_rule(rules)
        fake = rule.model_copy(
            update={"then_conclusions": {"formula": "麻黄汤", "strength": "主之"}}
        )
        _, repairs, must_reject = AutoRepairAgent().repair(fake)
        assert must_reject
        assert any("would_remove_formula" in r for r in repairs)

    def test_repair_reruns_schema_validation(self, rules):
        """P0-3: 修复产物必须重新通过 InitialRule schema 校验。"""
        rule = guizhi_rule(rules)
        repaired, _, _ = AutoRepairAgent().repair(rule)
        assert isinstance(repaired, InitialRule)

    def test_repair_cleans_fabricated_absent_terms(self, rules):
        """回归: repair 同样清理不在 span 内的否定条件。"""
        rule = guizhi_rule(rules)
        conds = dict(rule.if_conditions)
        conds["absent"] = ["虚构否定症状"]
        fake = rule.model_copy(update={"if_conditions": conds})
        repaired, repairs, must_reject = AutoRepairAgent().repair(fake)
        assert not must_reject
        assert "虚构否定症状" not in repaired.if_conditions.get("absent", [])
        assert any("absent" in r for r in repairs)


class TestMultiReviewerConsensus:
    def test_llm_reviewer_fail_blocks_release(self, index, rules):
        formula_dict = load_formula_dict()
        pipeline = ReviewPipeline(
            verifier=EvidenceVerifier(index),
            semantic=SemanticReviewer(),
            critic=ShanghanCritic(
                {f: m["channel_anchor"] for f, m in formula_dict["formulas"].items()}
            ),
            clause_channels={cid: c.channel for cid, c in index.items()},
            extra_reviewers=[
                lambda r: type("V", (), {"verdict": "fail",
                                          "problems": ["llm_reviewer_veto"]})()
            ],
        )
        ar = pipeline.review(guizhi_rule(rules))
        assert ar.release_level == "rejected"
        assert "llm_reviewer_veto" in ar.problems

    def test_llm_review_adapter_wires_gateway(self, index, rules):
        """llm_review 适配器: LLMGateway 评审真实接入 extra_reviewers,
        fail 判决拒绝规则,评审故障降级 warn 不阻断。"""
        from audit_chain import AuditLedger
        from hermes_llm import LLMGateway, StubLLMBackend
        from shanghan.llm_review import make_llm_reviewers

        backend = StubLLMBackend(
            responses=['{"verdict": "fail", "problems": ["IF与THEN不同分支"]}']
        )
        gw = LLMGateway(backend, ledger=AuditLedger(),
                        encounter_id="llmrev", retry_backoff=0)
        reviewers = make_llm_reviewers(gw, roles=("tcm_reviewer",))
        verdict = reviewers[0](guizhi_rule(rules))
        assert verdict.verdict == "fail"
        assert any("IF与THEN不同分支" in p for p in verdict.problems)
        # 审计入链(硬规则7: 经网关的调用自动携带审计)
        assert gw.cost_log.summary()["calls"] == 1

    def test_llm_review_adapter_error_degrades_to_warn(self, rules):
        from shanghan.llm_review import make_llm_reviewers

        class BrokenGateway:
            def structured_call(self, *a, **kw):
                raise TimeoutError("down")

        reviewers = make_llm_reviewers(BrokenGateway(), roles=("critic",))
        verdict = reviewers[0](guizhi_rule(rules))
        assert verdict.verdict == "warn"
        assert any("llm_reviewer_error" in p for p in verdict.problems)

    def test_default_reviewers_empty_without_model(self, monkeypatch):
        from shanghan.llm_review import default_llm_reviewers

        monkeypatch.delenv("HERMES_LLM_MODEL", raising=False)
        assert default_llm_reviewers() == []
