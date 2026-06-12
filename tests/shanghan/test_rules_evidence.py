"""branch 级规则抽取与证据回源(评审 P1-2/P1-3 + 对抗用例)。"""
import pytest

from shanghan.corpus import corpus_index
from shanghan.evidence import EvidenceVerifier
from shanghan.rules import InitialRule, InitialRuleExtractor


@pytest.fixture(scope="module")
def index():
    return corpus_index()


@pytest.fixture(scope="module")
def rules(index):
    return InitialRuleExtractor().extract_corpus(list(index.values()))


@pytest.fixture(scope="module")
def verifier(index):
    return EvidenceVerifier(index)


def rule_for_formula(rules, formula):
    return next(
        r for r in rules if r.then_conclusions.get("formula") == formula
    )


class TestExtraction:
    def test_rule_types_present(self, rules):
        types = {r.rule_type for r in rules}
        assert types == {
            "formula_pattern_rule", "contraindication_rule",
            "channel_outline_rule", "mistreatment_rule",
        }

    def test_six_channel_outlines(self, rules):
        channels = {
            r.then_conclusions["channel"]
            for r in rules if r.rule_type == "channel_outline_rule"
        }
        assert channels == {"taiyang", "yangming", "shaoyang", "taiyin",
                            "shaoyin", "jueyin"}

    def test_branch_level_evidence_not_full_clause(self, rules, index):
        """评审 P1-2: 多分支条文的 evidence_span 必须小于整条条文。"""
        wuling = rule_for_formula(rules, "五苓散")
        full = index[wuling.clause_id].text
        assert wuling.evidence_span != full
        assert len(wuling.evidence_span) < len(full)
        assert wuling.condition_span
        assert wuling.conclusion_span
        assert wuling.branch_id.endswith("_B2")

    def test_negated_terms_go_to_absent(self, rules):
        """208条: "不恶寒" → absent,不进 symptoms。"""
        dacheng = rule_for_formula(rules, "大承气汤")
        assert "恶寒" in dacheng.if_conditions.get("absent", [])
        assert "恶寒" not in dacheng.if_conditions.get("symptoms", [])

    def test_optional_symptoms_separated(self, rules):
        xiaoqinglong = rule_for_formula(rules, "小青龙汤")
        optional = xiaoqinglong.if_conditions.get("optional_symptoms", [])
        assert "渴" in optional
        assert "渴" not in xiaoqinglong.if_conditions.get("symptoms", [])

    def test_mistreatment_rule_has_prior(self, rules):
        guizhi_fuzi = rule_for_formula(rules, "桂枝加附子汤")
        assert guizhi_fuzi.rule_type == "mistreatment_rule"
        assert guizhi_fuzi.if_conditions.get("prior_treatment")


class TestEvidenceVerifier:
    def test_all_extracted_rules_verify(self, rules, verifier):
        for r in rules:
            report = verifier.verify(r)
            assert report.ok, (r.rule_id, report.problems)

    def test_fabricated_clause_id_fails(self, rules, verifier):
        fake = rules[0].model_copy(update={"clause_id": "SHL_999"})
        report = verifier.verify(fake)
        assert not report.ok
        assert "clause_id 不存在" in report.problems[0]

    def test_fabricated_symptom_fails(self, rules, verifier):
        rule = rule_for_formula(rules, "桂枝汤")
        conds = dict(rule.if_conditions)
        conds["symptoms"] = list(conds.get("symptoms", [])) + ["虚构症状"]
        fake = rule.model_copy(update={"if_conditions": conds})
        report = verifier.verify(fake)
        assert not report.ok
        assert any("虚构症状" in p for p in report.problems)

    def test_formula_outside_conclusion_span_fails(self, rules, verifier):
        """评审第六条: 方剂必须出现在 conclusion_span,而非 full_text。"""
        rule = rule_for_formula(rules, "桂枝汤")
        fake = rule.model_copy(
            update={"then_conclusions": {"formula": "麻黄汤", "strength": "主之"}}
        )
        report = verifier.verify(fake)
        assert not report.ok
        assert any("麻黄汤" in p for p in report.problems)

    def test_misaligned_offsets_fail(self, rules, verifier):
        rule = rules[0]
        fake = rule.model_copy(update={"evidence_offsets": (0, 1)})
        report = verifier.verify(fake)
        assert not report.ok

    def test_cross_branch_condition_fails(self, rules, verifier):
        """同条文跨分支污染: 烦躁不得眠(B1)绑到五苓散(B2)必须失败。"""
        wuling = rule_for_formula(rules, "五苓散")
        conds = dict(wuling.if_conditions)
        conds["symptoms"] = list(conds["symptoms"]) + ["烦躁不得眠"]
        fake = wuling.model_copy(update={"if_conditions": conds})
        report = verifier.verify(fake)
        assert not report.ok
