"""版本异文/注释对齐(评审第十二条)。

不再单靠 char-bigram Dice:
  锚点(条文号/共享方名/首尾短语)加权 + 一对一最大匹配约束
  + 人工校勘 whitelist/blacklist + 有序差异(保留顺序与上下文)。
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from shanghan.corpus import Clause, load_formula_dict

_PUNCT = re.compile(r"[,。;、:!?\s]")


@dataclass(frozen=True)
class VariantRecord:
    variant_id: str
    source: str
    text: str
    no: int | None = None
    kind: str = "variant"  # variant | commentary


@dataclass
class Alignment:
    variant_id: str
    clause_id: str | None
    score: float
    anchors: list[str] = field(default_factory=list)
    notable_differences: list[dict] = field(default_factory=list)
    status: str = "aligned"  # aligned | forced | unaligned


def load_variants(path: str | Path | None = None) -> list[VariantRecord]:
    if path is None:
        from hermes_contracts.paths import repo_root

        path = repo_root() / "knowledge" / "shanghan" / "variants.jsonl"
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(VariantRecord(**json.loads(line)))
    return records


def _bigrams(text: str) -> set[str]:
    clean = _PUNCT.sub("", text)
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def dice(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def ordered_diff(a: str, b: str) -> list[dict]:
    """有序差异(评审: 字符集合差异丢失顺序与上下文 → 用 opcodes 保留)。"""
    ops = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            ops.append({
                "op": tag,
                "base": a[i1:i2],
                "variant": b[j1:j2],
                "context": a[max(0, i1 - 3):i1],
            })
    return ops


class VariantAligner:
    def __init__(
        self,
        clauses: list[Clause],
        whitelist: dict[str, str] | None = None,
        blacklist: set[tuple[str, str]] | None = None,
        threshold: float = 0.45,
    ):
        """whitelist: variant_id → clause_id 强制对齐(人工校勘);
        blacklist: (variant_id, clause_id) 禁止对齐。"""
        self._clauses = clauses
        self._whitelist = whitelist or {}
        self._blacklist = blacklist or set()
        self._threshold = threshold
        self._formulas = sorted(load_formula_dict()["formulas"], key=len, reverse=True)

    # ------------------------------------------------------------------
    def _score(self, v: VariantRecord, c: Clause) -> tuple[float, list[str]]:
        anchors: list[str] = []
        score = dice(v.text, c.text)
        if v.no is not None and v.no == c.no:
            score += 1.0
            anchors.append("clause_no")
        shared = [
            f for f in self._formulas if f in v.text and f in c.text
        ]
        if shared:
            bonus = min(0.3 * len(shared), 0.6)
            score += bonus
            anchors.append(f"formula:{'/'.join(shared[:2])}")
        v_clean = _PUNCT.sub("", v.text)
        c_clean = _PUNCT.sub("", c.text)
        if len(v_clean) >= 4 and len(c_clean) >= 4:
            if v_clean[:4] == c_clean[:4]:
                score += 0.25
                anchors.append("head_phrase")
            if v_clean[-4:] == c_clean[-4:]:
                score += 0.25
                anchors.append("tail_phrase")
        return score, anchors

    def align(self, variants: list[VariantRecord]) -> list[Alignment]:
        results: dict[str, Alignment] = {}
        taken_clauses: set[str] = set()

        # 1) 人工校勘 whitelist 优先(强制一对一)
        for v in variants:
            forced = self._whitelist.get(v.variant_id)
            if forced:
                clause = next(c for c in self._clauses if c.clause_id == forced)
                results[v.variant_id] = Alignment(
                    variant_id=v.variant_id,
                    clause_id=forced,
                    score=float("inf"),
                    anchors=["whitelist"],
                    notable_differences=ordered_diff(clause.text, v.text),
                    status="forced",
                )
                taken_clauses.add(forced)

        # 2) 其余候选对按分数降序做一对一贪心最大匹配
        pairs: list[tuple[float, list[str], VariantRecord, Clause]] = []
        for v in variants:
            if v.variant_id in results:
                continue
            for c in self._clauses:
                if (v.variant_id, c.clause_id) in self._blacklist:
                    continue
                score, anchors = self._score(v, c)
                if score >= self._threshold:
                    pairs.append((score, anchors, v, c))
        pairs.sort(key=lambda x: -x[0])
        for score, anchors, v, c in pairs:
            if v.variant_id in results or c.clause_id in taken_clauses:
                continue
            results[v.variant_id] = Alignment(
                variant_id=v.variant_id,
                clause_id=c.clause_id,
                score=round(score, 4),
                anchors=anchors,
                notable_differences=ordered_diff(c.text, v.text),
                status="aligned",
            )
            taken_clauses.add(c.clause_id)

        # 3) 未达阈值或被占用 → unaligned(留人工校勘,不强行配对)
        out: list[Alignment] = []
        for v in variants:
            out.append(
                results.get(
                    v.variant_id,
                    Alignment(variant_id=v.variant_id, clause_id=None,
                              score=0.0, status="unaligned"),
                )
            )
        return out
