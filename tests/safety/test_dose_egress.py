"""硬规则1: 剂量出站扫描 — 覆盖率红线 100%,含绕行对抗用例。"""
import pytest

from hermes_guard.dose_egress import (
    REPLACEMENT,
    DoseViolation,
    EgressResult,
    find_dose_mentions,
    scan_outbound,
)


class TestBasicDosePatterns:
    @pytest.mark.parametrize(
        "text",
        [
            "建议每次服用200mg",
            "每次0.5g",
            "一次10ml",
            "胰岛素10单位",
            "口服300毫克",
            "滴眼液每次2滴",
            "两片",
            "每次半粒",
            "布洛芬400MG",
        ],
    )
    def test_dose_detected_and_redacted(self, text):
        result = scan_outbound(text)
        assert not result.clean
        assert REPLACEMENT in result.text
        # 原始剂量串不应残留
        for v in result.violations:
            assert v.text not in result.text or v.text == REPLACEMENT

    @pytest.mark.parametrize(
        "text",
        ["每日3次", "一日三次", "每天2次", "q8h", "qd", "bid", "tid", "qid"],
    )
    def test_frequency_detected(self, text):
        assert not scan_outbound(text).clean

    @pytest.mark.parametrize(
        "text",
        [
            "您的体温是38.5度",
            "血压120/80",
            "疼痛评分7分",
            "咳嗽3天了",
            "吃多少毫克需要医生确认",  # 无数字
            "请按医嘱用药",
            "",
        ],
    )
    def test_no_false_positive(self, text):
        result = scan_outbound(text)
        assert result.clean
        assert result.text == text


class TestEvasionAttempts:
    def test_fullwidth_digits(self):
        assert not scan_outbound("每次２００ｍｇ").clean

    def test_zero_width_space_between(self):
        assert not scan_outbound("每次200​mg").clean

    def test_space_between_number_and_unit(self):
        assert not scan_outbound("每次 200  mg").clean

    def test_chinese_numeral_unit(self):
        assert not scan_outbound("每次吃两片,一日三次").clean

    def test_split_unit_characters(self):
        assert not scan_outbound("每次200毫 克").clean

    def test_mixed_case_unit(self):
        assert not scan_outbound("每次200Mg").clean


class TestFilledSpans:
    def test_filled_span_allowed(self):
        text = "推荐剂量200mg,每6小时一次"
        spans = find_dose_mentions(text)
        assert spans
        filled = tuple(s for s, _ in spans)
        result = scan_outbound(text, filled)
        assert result.clean
        assert result.text == text

    def test_partial_fill_redacts_rest(self):
        text = "合法剂量200mg;非法剂量500mg"
        first_span = find_dose_mentions(text)[0][0]
        result = scan_outbound(text, (first_span,))
        assert len(result.violations) == 1
        assert "200mg" in result.text
        assert "500mg" not in result.text

    def test_no_overlap_helper(self):
        result = scan_outbound("每次200mg", ((100, 110),))
        assert not result.clean


class TestDataStructures:
    def test_violation_fields(self):
        result = scan_outbound("每次200mg")
        v = result.violations[0]
        assert isinstance(v, DoseViolation)
        assert v.pattern == "dose"
        assert v.text == "200mg"

    def test_freq_pattern_name(self):
        result = scan_outbound("每日3次")
        assert result.violations[0].pattern == "freq"

    def test_clean_result(self):
        r = EgressResult(text="ok")
        assert r.clean

    def test_multiple_redactions_order(self):
        text = "先吃200mg,再吃300mg,最后吃400mg"
        result = scan_outbound(text)
        assert len(result.violations) == 3
        assert result.text.count(REPLACEMENT) == 3
        assert "200" not in result.text
