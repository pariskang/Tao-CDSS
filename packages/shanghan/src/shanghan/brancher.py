"""ClauseBrancherAgent(评审 P1-1/P1-4):把条文切成语义分支,防止"条件污染"。

将「若A者,方1主之;若B者,方2主之;不可与方3」切成独立分支,每个分支携带:
  branch_id / condition_span / action_span / polarity / strength / 字符 offsets。
IF 条件后续只允许绑定到 branch.condition_span,而非整条条文(评审 P1-3)。

强度标记的语法位置识别(评审 P1-4):
  后置 "X主之";前置 "宜X/可与X/与X/属X";否定前置 "不可与X";
  治法禁忌 "不可发汗/不可下";指代禁忌 "不可服之"(回指前一分支主方)。
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
    strength: str | None = None  # 主之|宜|可与|与|属|不可与|不可服|不可发汗|...
    formula: str | None = None
    therapy: str | None = None  # 治法禁忌宾语: 发汗|下|吐
    referential: bool = False  # 禁忌为指代("不可服之"回指前方)
    optional_markers: list[str] = field(default_factory=list)
    prior_treatment: str | None = None


class ClauseBrancher:
    def __init__(self, formula_dict: dict | None = None):
        data = formula_dict or load_formula_dict()
        self._formulas = sorted(data["formulas"], key=len, reverse=True)
        markers = data["strength_markers"]
        self._after_markers = sorted(markers["positive_after"], key=len, reverse=True)
        self._before_markers = sorted(markers["positive_before"], key=len, reverse=True)
        self._neg_markers = sorted(markers["negative"], key=len, reverse=True)
        self._therapy_contra = sorted(
            data.get("therapy_contraindications", []), key=len, reverse=True
        )
        self._referential_contra = sorted(
            data.get("referential_contraindications", []), key=len, reverse=True
        )
        self._prior_markers = data["prior_treatment_markers"]

    # ------------------------------------------------------------------
    def split(self, clause: Clause) -> list[Branch]:
        segments = self._segments(clause.text)
        branches: list[Branch] = []
        pending_condition: tuple[str, int, int] | None = None
        last_formula: str | None = None
        n = 0
        for seg_text, seg_start, seg_end in segments:
            formula, f_idx = self._find_formula(seg_text)
            if formula is None:
                n += 1
                branch = self._formula_free_branch(
                    clause, seg_text, seg_start, seg_end, n, last_formula
                )
                branches.append(branch)
                if branch.polarity == "observation":
                    pending_condition = (seg_text, seg_start, seg_end)
                continue

            polarity, strength, position = self._polarity(seg_text, formula, f_idx)
            action_idx = f_idx
            if position == "before" and strength:
                action_idx = f_idx - len(strength)
            cond_text = seg_text[:action_idx].rstrip(",、 ")
            # 条件区间不得携带强度/否定标记本身
            for marker in sorted(
                self._neg_markers + self._before_markers + self._after_markers,
                key=len, reverse=True,
            ):
                if cond_text.endswith(marker):
                    cond_text = cond_text[: -len(marker)].rstrip(",、 ")
                    break
            cond_start, cond_end = seg_start, seg_start + len(cond_text)
            if not cond_text and pending_condition is not None:
                cond_text, cond_start, cond_end = pending_condition
            action_text = seg_text[action_idx:]
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
                    action_start=seg_start + action_idx,
                    action_end=seg_end,
                    polarity=polarity,
                    strength=strength,
                    formula=formula,
                    optional_markers=_OPTIONAL_RE.findall(cond_text),
                    prior_treatment=self._prior(cond_text),
                )
            )
            if polarity == "indicated":
                last_formula = formula
            pending_condition = None
        return branches

    # ------------------------------------------------------------------
    def _formula_free_branch(
        self, clause: Clause, seg_text: str, seg_start: int, seg_end: int,
        n: int, last_formula: str | None,
    ) -> Branch:
        base = dict(
            branch_id=f"{clause.clause_id}_B{n}",
            clause_id=clause.clause_id,
            text=seg_text,
            start=seg_start,
            end=seg_end,
            optional_markers=_OPTIONAL_RE.findall(seg_text),
            prior_treatment=self._prior(seg_text),
        )
        # 治法禁忌: "咽喉干燥者,不可发汗"
        for marker in self._therapy_contra:
            idx = seg_text.find(marker)
            if idx != -1:
                cond = seg_text[:idx].rstrip(",、 ")
                return Branch(
                    condition_span=cond,
                    condition_start=seg_start,
                    condition_end=seg_start + len(cond),
                    action_span=seg_text[idx:],
                    action_start=seg_start + idx,
                    action_end=seg_end,
                    polarity="contraindicated",
                    strength=marker,
                    therapy=marker.removeprefix("不可"),
                    **base,
                )
        # 指代禁忌: "若脉微弱,汗出恶风者,不可服之"(回指前一分支主方)
        if last_formula is not None:
            for marker in self._referential_contra:
                idx = seg_text.find(marker)
                if idx != -1:
                    cond = seg_text[:idx].rstrip(",、 ")
                    return Branch(
                        condition_span=cond,
                        condition_start=seg_start,
                        condition_end=seg_start + len(cond),
                        action_span=seg_text[idx:],
                        action_start=seg_start + idx,
                        action_end=seg_end,
                        polarity="contraindicated",
                        strength=marker,
                        formula=last_formula,
                        referential=True,
                        **base,
                    )
        # 普通观察分支(同时作为后续片段的悬挂条件)
        return Branch(
            condition_span=seg_text,
            condition_start=seg_start,
            condition_end=seg_end,
            action_span="",
            action_start=seg_end,
            action_end=seg_end,
            polarity="observation",
            **base,
        )

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
        """选取主方: 收集全部方剂提及,优先带强度/否定标记者;
        多个标记提及时取最后一个(评审第五条: 防止前置处置方被误判为主方)。"""
        mentions: list[tuple[str, int]] = []
        for f in self._formulas:  # 最长优先,避免子串误匹配
            start = 0
            while True:
                idx = segment.find(f, start)
                if idx == -1:
                    break
                inside_longer = any(
                    m_idx <= idx and idx + len(f) <= m_idx + len(m_f)
                    for m_f, m_idx in mentions
                )
                if not inside_longer:
                    mentions.append((f, idx))
                start = idx + len(f)
        if not mentions:
            return None, -1
        marked = [
            (f, idx) for f, idx in mentions if self._is_marked(segment, f, idx)
        ]
        pool = marked or mentions
        return max(pool, key=lambda x: x[1])

    def _is_marked(self, segment: str, formula: str, idx: int) -> bool:
        before = segment[:idx]
        after = segment[idx + len(formula):]
        if any(after.startswith(m) for m in self._after_markers):
            return True
        if any(before.endswith(m) for m in self._neg_markers + self._before_markers):
            return True
        return False

    def _polarity(
        self, segment: str, formula: str, f_idx: int
    ) -> tuple[str, str | None, str | None]:
        """返回 (polarity, strength, 标记位置 before|after|None)。"""
        before = segment[:f_idx]
        after = segment[f_idx + len(formula):]
        for neg in self._neg_markers:
            if before.endswith(neg):
                return "contraindicated", neg, "before"
        if after.startswith("不可"):
            return "contraindicated", "不可与", "after"
        for marker in self._after_markers:
            if after.startswith(marker):
                return "indicated", marker, "after"
        for marker in self._before_markers:
            if before.endswith(marker):
                return "indicated", marker, "before"
        # 方剂出现但无强度标记(如方后注),视为观察
        return "observation", None, None

    def _prior(self, text: str) -> str | None:
        for marker in self._prior_markers:
            if marker in text:
                return marker
        return None
