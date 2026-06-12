"""ClauseBrancherAgent(评审 P1-1):把条文切成语义分支,防止"条件污染"。

将「若A者,方1主之;若B者,方2主之;不可与方3」切成独立分支,每个分支携带:
  branch_id / condition_span / action_span / polarity / strength / 字符 offsets。
IF 条件后续只允许绑定到 branch.condition_span,而非整条条文(评审 P1-3)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from shanghan.corpus import Clause, load_formula_dict

#: 或然证标志: "或X,或Y…或Z者" 列出的症状属可选兼证,不算必要条件
_OPTIONAL_RE = re.compile(r"或([^,。;、]{1,8})")

_SEGMENT_SPLIT = re.compile(r"[;。]")


@dataclass
class Branch:
    branch_id: str
    clause_id: str
    text: str
    start: int
    end: int
    condition_span: str
    condition_start: int
    condition_end: int
    action_span: str
    action_start: int
    action_end: int
    polarity: str  # indicated | contraindicated | observation
    strength: str | None = None  # 主之|宜|可与|与|属|不可与|None
    formula: str | None = None
    optional_markers: list[str] = field(default_factory=list)
    prior_treatment: str | None = None


class ClauseBrancher:
    def __init__(self, formula_dict: dict | None = None):
        data = formula_dict or load_formula_dict()
        self._formulas = sorted(data["formulas"], key=len, reverse=True)
        self._pos_markers = data["strength_markers"]["positive"]
        self._neg_markers = data["strength_markers"]["negative"]
        self._prior_markers = data["prior_treatment_markers"]

    # ------------------------------------------------------------------
    def split(self, clause: Clause) -> list[Branch]:
        segments = self._segments(clause.text)
        branches: list[Branch] = []
        pending_condition: tuple[str, int, int] | None = None
        n = 0
        for seg_text, seg_start, seg_end in segments:
            formula, f_idx = self._find_formula(seg_text)
            if formula is None:
                # 无方剂动作的片段: 既是观察分支,也作为后续片段的悬挂条件
                n += 1
                branches.append(
                    Branch(
                        branch_id=f"{clause.clause_id}_B{n}",
                        clause_id=clause.clause_id,
                        text=seg_text,
                        start=seg_start,
                        end=seg_end,
                        condition_span=seg_text,
                        condition_start=seg_start,
                        condition_end=seg_end,
                        action_span="",
                        action_start=seg_end,
                        action_end=seg_end,
                        polarity="observation",
                        optional_markers=_OPTIONAL_RE.findall(seg_text),
                        prior_treatment=self._prior(seg_text),
                    )
                )
                pending_condition = (seg_text, seg_start, seg_end)
                continue

            polarity, strength = self._polarity(seg_text, formula, f_idx)
            cond_text = seg_text[:f_idx].rstrip(",、 ")
            # 条件区间不得携带强度/否定标记本身(如"不可与")
            for marker in sorted(
                self._neg_markers + self._pos_markers, key=len, reverse=True
            ):
                if cond_text.endswith(marker):
                    cond_text = cond_text[: -len(marker)].rstrip(",、 ")
                    break
            cond_start, cond_end = seg_start, seg_start + len(cond_text)
            if not cond_text and pending_condition is not None:
                cond_text, cond_start, cond_end = pending_condition
            action_text = seg_text[f_idx:]
            n += 1
            branches.append(
                Branch(
                    branch_id=f"{clause.clause_id}_B{n}",
                    clause_id=clause.clause_id,
                    text=seg_text,
                    start=seg_start,
                    end=seg_end,
                    condition_span=cond_text,
                    condition_start=cond_start,
                    condition_end=cond_end,
                    action_span=action_text,
                    action_start=seg_start + f_idx,
                    action_end=seg_end,
                    polarity=polarity,
                    strength=strength,
                    formula=formula,
                    optional_markers=_OPTIONAL_RE.findall(cond_text),
                    prior_treatment=self._prior(cond_text),
                )
            )
            pending_condition = None
        return branches

    # ------------------------------------------------------------------
    @staticmethod
    def _segments(text: str) -> list[tuple[str, int, int]]:
        coarse = []
        start = 0
        for m in _SEGMENT_SPLIT.finditer(text):
            seg = text[start:m.start()]
            if seg.strip():
                coarse.append((seg, start))
            start = m.end()
        tail = text[start:]
        if tail.strip():
            coarse.append((tail, start))
        # 细分: ",若" 视为分支边界("若…者"开启新语义分支)
        segments: list[tuple[str, int, int]] = []
        for seg, s in coarse:
            pos = 0
            while True:
                idx = seg.find(",若", pos)
                if idx == -1:
                    part = seg[pos:]
                    if part.strip():
                        segments.append((part, s + pos, s + len(seg)))
                    break
                part = seg[pos:idx]
                if part.strip():
                    segments.append((part, s + pos, s + idx))
                pos = idx + 1  # 跳过逗号,保留"若"
        return segments

    def _find_formula(self, segment: str) -> tuple[str | None, int]:
        for f in self._formulas:
            idx = segment.find(f)
            if idx != -1:
                return f, idx
        return None, -1

    def _polarity(self, segment: str, formula: str, f_idx: int) -> tuple[str, str | None]:
        window_before = segment[max(0, f_idx - 4):f_idx]
        after = segment[f_idx + len(formula):]
        for neg in self._neg_markers:
            if neg in window_before or after.startswith(("不可与", "不可")):
                return "contraindicated", "不可与"
        if "不可与" in segment[:f_idx]:
            return "contraindicated", "不可与"
        for marker in self._pos_markers:
            if after.startswith(marker):
                return "indicated", marker
        # 方剂出现但无强度标记(如方后注),视为观察
        return "observation", None

    def _prior(self, text: str) -> str | None:
        for marker in self._prior_markers:
            if marker in text:
                return marker
        return None
