"""P0-4: route() 的每个 handler 在 ask() 中都有真实 app 调用;
P0-5: 患者端递归脱敏。"""
import pytest

from shanghan.patient_safety import redact_payload
from shanghan.pipeline import ShanghanPipeline
from shanghan.skill_rag import HANDLERS, SkillRAG


@pytest.fixture(scope="module")
def rag():
    return SkillRAG(ShanghanPipeline().run())


class TestRouting:
    @pytest.mark.parametrize(
        "question,expected",
        [
            ("恶寒发热无汗脉浮紧应该匹配什么方证?", "match"),
            ("桂枝汤的方证是什么", "formula"),
            ("误治之后怎么救?", "mistreatment"),
            ("桂枝汤有什么禁忌?", "contraindication"),
            ("无汗而喘怎么治?", "therapy"),
            ("生成论文草稿", "paper"),
            ("第13条原文是什么", "clause"),
            ("六经提纲分别是什么", "six_channel"),
            ("桂枝汤和麻黄汤怎么鉴别", "differential"),
            ("给我讲讲怕冷的科普", "patient"),
            ("随便聊聊头痛", "generic"),
        ],
    )
    def test_route(self, rag, question, expected):
        assert rag.route(question) == expected


class TestHandlersAllWired:
    def test_every_handler_callable(self, rag):
        for handler in HANDLERS:
            assert hasattr(rag, f"_h_{handler}"), f"未实现 handler: {handler}"

    def test_match_calls_formula_matcher(self, rag):
        out = rag.ask("头痛发热,身疼腰痛,无汗而喘,应该匹配什么方证?")
        assert out["handler"] == "match"
        formulas = [m["formula"] for m in out["matched_formula_patterns"]]
        assert formulas[0] == "麻黄汤"
        assert out["matched_formula_patterns"][0]["evidence"]

    def test_formula_handler_returns_rules_and_evidence(self, rag):
        out = rag.ask("桂枝汤的方证是什么")
        assert out["formula"] == "桂枝汤"
        assert out["rules"]
        assert out["pattern"]["contraindications"] == ["酒客病"]
        assert any("SHL_013" == e["clause_id"] for e in out["evidence"])

    def test_mistreatment_handler(self, rag):
        out = rag.ask("误治之后怎么救?")
        assert out["rules"]
        assert all(r["rule_type"] == "mistreatment_rule" for r in out["rules"])

    def test_contraindication_handler(self, rag):
        out = rag.ask("桂枝汤有什么禁忌?")
        assert any(
            r["then_conclusions"].get("forbidden_formula") == "桂枝汤"
            for r in out["rules"]
        )

    def test_therapy_handler(self, rag):
        out = rag.ask("无汗而喘怎么治?")
        assert "channels" in out
        assert out["matched_formula_patterns"]

    def test_paper_handler_is_draft(self, rag):
        out = rag.ask("生成论文草稿")
        assert out["kind"] == "paper_draft"
        assert "草稿" in out["title"]

    def test_clause_handler(self, rag):
        out = rag.ask("第13条原文是什么")
        assert out["clauses"][0]["no"] == 13

    def test_six_channel_handler(self, rag):
        out = rag.ask("六经提纲分别是什么")
        assert "taiyang" in out["channels"]
        assert "posthoc" in out["answer"]

    def test_differential_handler_exact_pair(self, rag):
        out = rag.ask("桂枝汤和麻黄汤怎么鉴别")
        pair = out["pairs"][0]
        assert {pair["formula_a"], pair["formula_b"]} == {"桂枝汤", "麻黄汤"}

    def test_generic_handler_evidence_search(self, rag):
        out = rag.ask("随便聊聊头痛")
        assert out["handler"] == "generic"
        assert any("头痛" in e["text"] for e in out["evidence"])


class TestPatientGovernance:
    def test_patient_role_forces_patient_handler(self, rag):
        out = rag.ask("恶寒发热无汗,用什么方?", role="patient")
        assert out["handler"] == "patient"
        assert "matched_formula_patterns" not in out
        assert out["governance"]["role"] == "patient"

    def test_nested_redaction(self):
        """P0-5: 嵌套字段递归脱敏(评审第十一条的攻击场景)。"""
        payload = {
            "answer": "科普内容",
            "evidence": [
                {"text": "桂枝三两,芍药三两,甘草二两,生姜三两,大枣十二枚"}
            ],
            "deep": {"nested": {"composition": "桂枝三两", "note": "每次200mg"}},
        }
        out = redact_payload(payload)
        assert "三两" not in str(out)
        assert "composition" not in out["deep"]["nested"]
        assert "200mg" not in str(out)

    def test_forbidden_keys_stripped_any_depth(self):
        payload = {"a": [{"recommended_formulas": ["桂枝汤"], "keep": "ok"}]}
        out = redact_payload(payload)
        assert "recommended_formulas" not in out["a"][0]
        assert out["a"][0]["keep"] == "ok"

    def test_classical_dose_redacted_in_patient_reference(self, rag):
        out = rag.ask("给我讲讲怕冷的科普", role="patient")
        text = str(out)
        assert "三两" not in text
        assert "governance" in out
