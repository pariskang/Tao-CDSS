"""版本异文/注释对齐(评审第十二条): 锚点 + 一对一约束 + 校勘清单 + 有序差异。"""
import pytest

from shanghan.corpus import load_corpus
from shanghan.variants import VariantAligner, load_variants, ordered_diff


@pytest.fixture(scope="module")
def clauses():
    return load_corpus()


@pytest.fixture(scope="module")
def variants():
    return load_variants()


@pytest.fixture(scope="module")
def alignments(clauses, variants):
    return {a.variant_id: a for a in VariantAligner(clauses).align(variants)}


class TestAnchors:
    def test_clause_no_anchor(self, alignments):
        a = alignments["DEMO_V_001"]
        assert a.clause_id == "SHL_001"
        assert "clause_no" in a.anchors

    def test_formula_and_phrase_anchors(self, alignments):
        a = alignments["DEMO_V_013"]
        assert a.clause_id == "SHL_013"
        assert any(anchor.startswith("formula:") for anchor in a.anchors)

    def test_commentary_aligns_to_source_clause(self, alignments):
        assert alignments["DEMO_C_012"].clause_id == "SHL_012"

    def test_unrelated_text_unaligned(self, alignments):
        a = alignments["DEMO_X_999"]
        assert a.status == "unaligned"
        assert a.clause_id is None


class TestOneToOneConstraint:
    def test_no_clause_claimed_twice(self, alignments):
        claimed = [a.clause_id for a in alignments.values() if a.clause_id]
        assert len(claimed) == len(set(claimed))

    def test_blacklist_blocks_pair(self, clauses, variants):
        aligner = VariantAligner(
            clauses, blacklist={("DEMO_V_013", "SHL_013")}
        )
        out = {a.variant_id: a for a in aligner.align(variants)}
        assert out["DEMO_V_013"].clause_id != "SHL_013"

    def test_whitelist_forces_pair(self, clauses, variants):
        aligner = VariantAligner(clauses, whitelist={"DEMO_V_013": "SHL_012"})
        out = {a.variant_id: a for a in aligner.align(variants)}
        assert out["DEMO_V_013"].clause_id == "SHL_012"
        assert out["DEMO_V_013"].status == "forced"
        # 一对一: 被强制占用的条文不可再分配
        assert out["DEMO_C_012"].clause_id != "SHL_012"


class TestOrderedDiff:
    def test_diff_preserves_order_and_context(self):
        ops = ordered_diff("太阳之为病,脉浮,头项强痛而恶寒。",
                           "太阳之为病,脉浮,头项强痛而恶寒也。")
        assert ops
        assert ops[0]["op"] in ("insert", "replace")
        assert "也" in ops[0]["variant"]
        assert "context" in ops[0]

    def test_alignment_carries_diff(self, alignments):
        a = alignments["DEMO_V_001"]
        assert any("也" in d["variant"] for d in a.notable_differences)
