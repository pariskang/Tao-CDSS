"""协议 L8.1: class 3-4 内容出现在患者端通道即拦截。"""
import pytest

from hermes_contracts import CDSSClaim
from hermes_guard.scope_checker import check_claim, check_text, classify_text


class TestClassification:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("情况紧急,请立即拨打120", 5),
            ("建议您马上去急诊", 5),
            ("建议服用布洛芬", 4),
            ("为您开具处方", 4),
            ("用法用量请遵医嘱调整", 4),
            ("每天吃两次饭后服", 4),
            ("您患有肺炎", 3),
            ("诊断为上呼吸道感染", 3),
            ("考虑为心绞痛的可能性大", 3),
            ("鉴别诊断包括肺栓塞", 3),
            ("建议完善胸部CT检查", 3),
            ("健康教育:注意休息,多喝水", 1),
            ("您刚才说咳嗽三天,对吗?", 0),
        ],
    )
    def test_classify(self, text, expected):
        cls, _ = classify_text(text)
        assert cls == expected

    def test_dose_pattern_is_class4(self):
        cls, reasons = classify_text("可以试试200mg")
        assert cls == 4
        assert "dose_pattern" in reasons


class TestChannelEnforcement:
    @pytest.mark.parametrize(
        "text", ["您患有肺炎", "建议服用布洛芬", "建议完善胸部CT检查"]
    )
    def test_patient_channel_blocks_class3plus(self, text):
        decision = check_text(text, channel="patient")
        assert not decision.allowed
        assert decision.output_class >= 3

    @pytest.mark.parametrize(
        "text", ["您患有肺炎", "建议服用布洛芬", "鉴别诊断包括肺栓塞"]
    )
    def test_doctor_channel_allows(self, text):
        assert check_text(text, channel="doctor").allowed

    def test_patient_channel_allows_education(self):
        decision = check_text("注意休息,多喝水", channel="patient")
        assert decision.allowed
        assert decision.output_class == 1

    def test_patient_channel_allows_emergency_script(self):
        decision = check_text("情况紧急,请立即拨打120", channel="patient")
        assert decision.allowed
        assert decision.output_class == 5

    def test_patient_channel_allows_class0(self):
        assert check_text("您刚才说咳嗽三天,对吗?", channel="patient").allowed


class TestClaimEnforcement:
    def _claim(self, text: str, output_class: int) -> CDSSClaim:
        return CDSSClaim(
            claim_id="c1", text=text, claim_type="differential",
            output_class=output_class,
        )

    def test_claim_declared_class_blocks_patient(self):
        # 文本伪装成科普,但声明 class=3 → 取更严者
        decision = check_claim(self._claim("相关说明", 3), channel="patient")
        assert not decision.allowed
        assert decision.output_class == 3

    def test_claim_text_class_overrides_low_declaration(self):
        decision = check_claim(self._claim("您患有肺炎", 1), channel="patient")
        assert not decision.allowed
        assert decision.output_class == 3

    def test_claim_allowed_on_doctor_channel(self):
        assert check_claim(self._claim("您患有肺炎", 3), channel="doctor").allowed

    def test_low_class_claim_allowed_patient(self):
        assert check_claim(self._claim("注意休息", 1), channel="patient").allowed
