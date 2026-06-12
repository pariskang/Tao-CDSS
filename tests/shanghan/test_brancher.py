"""分支解析(评审 P1-1): 防止跨分支条件污染。"""
from shanghan.brancher import ClauseBrancher
from shanghan.corpus import corpus_index


def branches_of(clause_id: str):
    return ClauseBrancher().split(corpus_index()[clause_id])


class TestMultiBranchIsolation:
    def test_clause_71_two_branches_no_pollution(self):
        """71条: 烦躁不得眠属第一分支,五苓散条件只含若脉浮…消渴者。"""
        branches = branches_of("SHL_071")
        wuling = next(b for b in branches if b.formula == "五苓散")
        assert "烦躁" not in wuling.condition_span
        assert "脉浮" in wuling.condition_span
        assert "消渴" in wuling.condition_span
        first = branches[0]
        assert first.polarity == "observation"
        assert first.prior_treatment == "发汗后"

    def test_clause_34_mistreatment_branch_separated(self):
        branches = branches_of("SHL_034")
        gegen = next(b for b in branches if b.formula == "葛根黄芩黄连汤")
        assert gegen.condition_span == "喘而汗出者"
        assert "利遂不止" not in gegen.condition_span
        obs = branches[0]
        assert obs.prior_treatment in ("医反下之", "反下之")

    def test_clause_273_ruo_subbranch_split(self):
        """273条: ",若下之,必胸下结硬" 切出独立分支,提纲不被污染。"""
        branches = branches_of("SHL_273")
        assert len(branches) == 2
        outline, warn = branches
        assert "胸下结硬" not in outline.condition_span
        assert warn.prior_treatment == "若下之"


class TestPolarityAndStrength:
    def test_indicated_zhuzhi(self):
        b = next(x for x in branches_of("SHL_013") if x.formula == "桂枝汤")
        assert b.polarity == "indicated"
        assert b.strength == "主之"

    def test_contraindicated(self):
        b = next(x for x in branches_of("SHL_017") if x.formula == "桂枝汤")
        assert b.polarity == "contraindicated"
        assert b.strength == "不可与"
        assert "不可与" not in b.condition_span  # 条件区间不携带否定标记
        assert b.condition_span == "酒客病"

    def test_longest_formula_wins(self):
        b = next(x for x in branches_of("SHL_014") if x.formula)
        assert b.formula == "桂枝加葛根汤"

    def test_prior_formula_mention_in_condition(self):
        """26条: 服桂枝汤为前置处置,主方为白虎加人参汤。"""
        b = next(x for x in branches_of("SHL_026") if x.polarity == "indicated")
        assert b.formula == "白虎加人参汤"
        assert b.prior_treatment == "服桂枝汤"

    def test_longer_prior_formula_does_not_steal_main(self):
        """回归: 前置方剂比主方更长时,主方仍取带强度标记的最后提及。"""
        from shanghan.corpus import Clause

        synthetic = Clause(
            clause_id="SHL_TEST", no=999, channel="taiyang", section="test",
            text="服桂枝加附子汤后,头痛发热,无汗而喘者,麻黄汤主之。",
        )
        b = next(x for x in ClauseBrancher().split(synthetic) if x.polarity == "indicated")
        assert b.formula == "麻黄汤"
        assert b.strength == "主之"


class TestNewGrammarForms:
    def test_before_marker_yi(self):
        """42条: "宜桂枝汤" 前置强度标记,结论区间含标记。"""
        b = next(x for x in branches_of("SHL_042") if x.formula == "桂枝汤")
        assert b.polarity == "indicated"
        assert b.strength == "宜"
        assert b.action_span.startswith("宜桂枝汤")
        assert "宜" not in b.condition_span[-1:]

    def test_before_marker_yu(self):
        """103条: "先与小柴胡汤"/"与大柴胡汤" 前置 与。"""
        branches = branches_of("SHL_103")
        xiao = next(x for x in branches if x.formula == "小柴胡汤")
        da = next(x for x in branches if x.formula == "大柴胡汤")
        assert xiao.strength == "与"
        assert da.strength == "与"
        assert da.condition_span == "呕不止,心下急,郁郁微烦者"
        # 跨分支隔离: 第一分支条件不污染第二分支
        assert "柴胡证仍在" not in da.condition_span

    def test_referential_contraindication(self):
        """38条: "不可服之" 回指大青龙汤。"""
        branches = branches_of("SHL_038")
        contra = next(x for x in branches if x.polarity == "contraindicated")
        assert contra.formula == "大青龙汤"
        assert contra.referential
        assert contra.condition_span == "若脉微弱,汗出恶风者"
        assert contra.action_span.startswith("不可服")

    def test_therapy_contraindication(self):
        """83条: "不可发汗" 治法禁忌(无方剂宾语)。"""
        b = branches_of("SHL_083")[0]
        assert b.polarity == "contraindicated"
        assert b.therapy == "发汗"
        assert b.formula is None
        assert b.condition_span == "咽喉干燥者"

    def test_therapy_contraindication_with_consequence(self):
        b = branches_of("SHL_084")[0]
        assert b.therapy == "发汗"
        assert b.condition_span == "淋家"
        assert "便血" in b.action_span  # 后果保留在结论区间


class TestOptionalAndOffsets:
    def test_optional_symptoms_clause_40(self):
        b = next(x for x in branches_of("SHL_040") if x.formula == "小青龙汤")
        assert any("渴" in m for m in b.optional_markers)
        assert any("喘" in m for m in b.optional_markers)

    def test_offsets_align_with_text(self):
        clause = corpus_index()["SHL_013"]
        for b in ClauseBrancher().split(clause):
            assert clause.text[b.start:b.end] == b.text
            assert clause.text[b.condition_start:b.condition_end] == b.condition_span

    def test_outline_clause_single_observation(self):
        branches = branches_of("SHL_001")
        assert len(branches) == 1
        assert branches[0].polarity == "observation"
