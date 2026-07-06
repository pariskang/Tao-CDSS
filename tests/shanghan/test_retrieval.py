"""BM25 检索(字符 bigram)单测: 排序质量/确定性/退化输入。"""
from shanghan.retrieval import BM25Index, tokenize


class TestTokenize:
    def test_bigrams(self):
        assert tokenize("头痛发热") == ["头痛", "痛发", "发热"]

    def test_punctuation_stripped(self):
        assert tokenize("头痛,发热。") == ["头痛", "痛发", "发热"]

    def test_single_char_fallback(self):
        assert tokenize("痛") == ["痛"]
        assert tokenize(",") == []


class TestBM25:
    def _index(self):
        return BM25Index({
            "d1": "太阳病,头痛发热,汗出恶风,桂枝汤主之",
            "d2": "太阳病,头痛发热,身疼腰痛,无汗而喘,麻黄汤主之",
            "d3": "少阴病,脉微细,但欲寐",
            "d4": "伤寒表不解,心下有水气,干呕发热而咳,小青龙汤主之",
        })

    def test_relevant_doc_ranks_first(self):
        top = self._index().search("无汗而喘怎么办")
        assert top[0][0] == "d2"

    def test_irrelevant_terms_no_match(self):
        assert self._index().search("完全无关的现代词汇量子计算") == []

    def test_deterministic_tie_break(self):
        idx = BM25Index({"a": "头痛", "b": "头痛"})
        r1 = idx.search("头痛")
        r2 = idx.search("头痛")
        assert r1 == r2
        assert [d for d, _ in r1] == ["a", "b"]

    def test_empty_index(self):
        assert BM25Index({}).search("头痛") == []

    def test_top_k_limit(self):
        assert len(self._index().search("发热", top_k=2)) <= 2


class TestHybridRanking:
    def test_generic_falls_back_to_bm25_without_entities(self, ):
        """无实体词的问句此前返回空,现经 BM25 兜底召回。"""
        from shanghan.runtime import default_rag

        rag = default_rag()
        out = rag.ask("但欲寐是怎么回事")
        assert out["evidence"], "BM25 兜底应召回条文"
        assert any("但欲寐" in e["text"] for e in out["evidence"])
