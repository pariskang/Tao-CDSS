from hermes_contracts import SymptomStatus
from hermes_voice.asr_client import StubASRClient
from hermes_voice.mvsl import confidence
from hermes_voice.mvsl.confusion_guard import ConfusionGuard
from hermes_voice.mvsl.dialect_mapper import DialectMapper
from hermes_voice.mvsl.hedge import detect
from hermes_voice.mvsl.numeric_readback import check as readback_check


class TestHedge:
    def test_plain_denial(self):
        assert detect("没有发烧").status == SymptomStatus.DENIED

    def test_leading_negation(self):
        assert detect("不痛").status == SymptomStatus.DENIED

    def test_probable_denial_capped(self):
        result = detect("好像没有出汗")
        assert result.status == SymptomStatus.PROBABLY_DENIED
        assert result.confidence_cap == 0.7

    def test_mild_presence(self):
        result = detect("有点咳嗽")
        assert result.status == SymptomStatus.PRESENT
        assert result.severity == "mild"
        assert result.uncertain

    def test_unknown(self):
        assert detect("记不清了").status == SymptomStatus.UNKNOWN

    def test_default_present(self):
        assert detect("三天前开始的").status == SymptomStatus.PRESENT

    def test_mild_with_trailing_negation_still_present(self):
        assert detect("有点痛,不太严重").status == SymptomStatus.PRESENT

    def test_term_window_denied(self):
        assert detect("胸口痛,但是没有出汗", term="出汗").status == SymptomStatus.DENIED

    def test_term_window_present(self):
        assert detect("胸口痛还出汗", term="出汗").status == SymptomStatus.PRESENT

    def test_term_probable_denial(self):
        assert (
            detect("应该没有麻木吧", term="麻木").status
            == SymptomStatus.PROBABLY_DENIED
        )

    def test_term_mild(self):
        assert detect("有点气紧", term="气紧").severity == "mild"

    def test_term_unknown_marker(self):
        assert detect("记不清有没有发烧", term="发烧").status == SymptomStatus.UNKNOWN

    def test_term_absent(self):
        assert detect("咳嗽", term="出汗").status == SymptomStatus.UNKNOWN


class TestConfidencePolicy:
    def test_critical_below_090_confirm(self):
        assert confidence.decide(0.89, critical=True) == "confirm"

    def test_high_accept(self):
        assert confidence.decide(0.9) == "accept"

    def test_medium_uncertain(self):
        assert confidence.decide(0.7) == "accept_uncertain"

    def test_low_reask(self):
        assert confidence.decide(0.5) == "reask"

    def test_critical_high_accept(self):
        assert confidence.decide(0.95, critical=True) == "accept"


class TestConfusionGuard:
    def test_confusion_pair_double_confirm(self):
        guard = ConfusionGuard.from_yaml()
        req = guard.check("阿糖腺苷", asr_confidence=0.99)
        assert req is not None
        assert "阿糖胞苷" in req.prompt
        assert req.kind == "drug_name"

    def test_low_confidence_confirm(self):
        guard = ConfusionGuard({})
        req = guard.check("布洛芬", asr_confidence=0.8)
        assert req is not None
        assert "布洛芬" in req.prompt

    def test_clean_pass(self):
        guard = ConfusionGuard({})
        assert guard.check("布洛芬", asr_confidence=0.99) is None


class TestNumericReadback:
    def test_numbers_trigger_readback(self):
        req = readback_check("体温38.5,血压140")
        assert req is not None
        assert req.values == ("38.5", "140")
        assert "38.5" in req.prompt

    def test_no_numbers_no_readback(self):
        assert readback_check("头有点晕") is None


class TestDialectMapper:
    def test_ambiguous_mapping(self):
        mapper = DialectMapper.from_yaml()
        mappings = mapper.find("心口疼得厉害")
        m = next(m for m in mappings if m.term == "心口疼")
        assert m.ambiguous

    def test_longest_term_wins(self):
        mapper = DialectMapper.from_yaml()
        terms = [m.term for m in mapper.find("心口疼")]
        assert "心口疼" in terms
        assert "心口" not in terms


class TestStubASR:
    def test_stream_yields_script(self):
        stub = StubASRClient(["第一句", ("第二句", 0.6)])
        results = list(stub.stream())
        assert [r.text for r in results] == ["第一句", "第二句"]
        assert results[0].confidence == 0.95
        assert results[1].confidence == 0.6
        assert results[1].utterance_id == "utt_001"
