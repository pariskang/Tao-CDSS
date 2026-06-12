import pytest

from mcp_servers.calculator.server import curb65, ecog, nrs_pain
from mcp_servers.drug_safety.server import (
    check_allergy,
    check_interaction,
    get_dose_range,
)
from mcp_servers.terminology.server import map_drug_name, normalize_symptom
from mcp_servers.triage.server import red_flag_check


class TestCalculator:
    @pytest.mark.parametrize(
        "score,band,flag",
        [(0, "none", False), (1, "mild", False), (3, "mild", False),
         (4, "moderate", False), (6, "moderate", False), (7, "severe", True),
         (10, "severe", True)],
    )
    def test_nrs_bands(self, score, band, flag):
        result = nrs_pain(score)
        assert result["band"] == band
        assert result["nursing_flag"] is flag

    @pytest.mark.parametrize("bad", [-1, 11, 3.5, "7", True])
    def test_nrs_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            nrs_pain(bad)

    @pytest.mark.parametrize("grade,limited", [(0, False), (2, False), (3, True), (5, True)])
    def test_ecog(self, grade, limited):
        result = ecog(grade)
        assert result["severely_limited"] is limited
        assert result["description"]

    @pytest.mark.parametrize("bad", [-1, 6, "3"])
    def test_ecog_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            ecog(bad)

    @pytest.mark.parametrize(
        "flags,score,band",
        [
            ((False,) * 5, 0, "low"),
            ((True, False, False, False, False), 1, "low"),
            ((True, True, False, False, False), 2, "intermediate"),
            ((True, True, True, False, False), 3, "high"),
            ((True,) * 5, 5, "high"),
        ],
    )
    def test_curb65(self, flags, score, band):
        result = curb65(*flags)
        assert result["score"] == score
        assert result["risk_band"] == band

    def test_curb65_no_disposition_text(self):
        # 禁止直接生成处置结论文本
        result = curb65(True, True, True, True, True)
        assert set(result) == {"score", "risk_band"}


class TestTerminology:
    def test_ambiguous_dialect_term(self):
        result = normalize_symptom("心口疼得很", dialect_region="sichuan")
        match = next(m for m in result["matches"] if m["term"] == "心口疼")
        assert match["ambiguous"]
        assert match["concept_id"] is None
        assert set(match["candidates"]) == {"上腹痛", "胸痛"}

    def test_unambiguous_term(self):
        result = normalize_symptom("有点气紧")
        match = result["matches"][0]
        assert not match["ambiguous"]
        assert match["concept_id"] == "呼吸困难"

    def test_drug_confusion_pair(self):
        result = map_drug_name("阿糖腺苷")
        assert result["requires_double_confirm"]
        assert "阿糖胞苷" in result["confusion_pairs"]

    def test_drug_without_confusion(self):
        result = map_drug_name("布洛芬")
        assert not result["requires_double_confirm"]
        assert result["confusion_pairs"] == []


class TestTriage:
    def test_wraps_redflag_engine(self):
        result = red_flag_check(["突然剧烈头痛"])
        assert result["level"] == "E1"
        assert result["hits"][0]["rule_id"] == "thunderclap_headache"

    def test_benign(self):
        assert red_flag_check(["有点鼻塞"])["level"] is None


class TestDrugSafety:
    def test_allergy_conflict(self):
        assert check_allergy("青霉素", ["青霉素", "磺胺"])["allergy_conflict"]
        assert not check_allergy("布洛芬", ["青霉素"])["allergy_conflict"]

    def test_interaction(self):
        found = check_interaction(["warfarin", "aspirin", "x"])["interactions"]
        assert found and found[0]["risk"]
        assert check_interaction(["x", "y"])["interactions"] == []

    def test_dose_range_structured_only(self):
        result = get_dose_range("ibuprofen", "adult")
        assert result["dose"]["max_mg"] == 400
        assert result["source"] == "drug_safety_server"

    def test_unknown_drug_returns_none_never_guesses(self):
        assert get_dose_range("unknown_drug")["dose"] is None
