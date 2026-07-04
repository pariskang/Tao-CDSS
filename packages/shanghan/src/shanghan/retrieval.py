"""BM25 条文检索(Okapi BM25, Robertson & Walker 1994; RSJ 权重)。

中文免分词方案: 字符二元组(character bigram)作为词项——古汉语单字多义,
bigram 在中文 IR 中稳定优于 unigram(Nie & Ren, IP&M 1999 起的经典结论)。
纯 Python 实现,零新依赖;倒排索引在构建时一次建成,查询 O(query_terms)。
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_PUNCT = re.compile(r"[,。;、:!?\s,.;:!?()()\[\]【】\"'「」『』]")


def tokenize(text: str) -> list[str]:
    """字符 bigram 词项化(过滤标点;不足两字时退回单字)。"""
    clean = _PUNCT.sub("", text)
    if len(clean) < 2:
        return [clean] if clean else []
    return [clean[i:i + 2] for i in range(len(clean) - 1)]


class BM25Index:
    def __init__(
        self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75
    ):
        self._k1 = k1
        self._b = b
        self._doc_len: dict[str, int] = {}
        self._tf: dict[str, dict[str, int]] = {}
        self._df: Counter = Counter()
        self._postings: dict[str, list[str]] = defaultdict(list)
        for doc_id, text in docs.items():
            terms = tokenize(text)
            self._doc_len[doc_id] = len(terms)
            tf = Counter(terms)
            self._tf[doc_id] = dict(tf)
            for term in tf:
                self._df[term] += 1
                self._postings[term].append(doc_id)
        self._n_docs = len(docs)
        self._avgdl = (
            sum(self._doc_len.values()) / self._n_docs if self._n_docs else 0.0
        )

    def _idf(self, term: str) -> float:
        # BM25 概率 IDF(+0.5 平滑),负值截断为 0(高频停用 bigram)
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return max(0.0, math.log((self._n_docs - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """返回 [(doc_id, score)] 按分数降序;同分按 doc_id 保证确定性。"""
        scores: dict[str, float] = defaultdict(float)
        for term in set(tokenize(query)):
            idf = self._idf(term)
            if idf == 0.0:
                continue
            for doc_id in self._postings.get(term, ()):
                tf = self._tf[doc_id][term]
                dl = self._doc_len[doc_id]
                denom = tf + self._k1 * (
                    1 - self._b + self._b * dl / self._avgdl
                )
                scores[doc_id] += idf * tf * (self._k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:top_k]
