"""实体抽取(最长优先词典匹配 + 否定感知)。

定位说明: 这是确定性抽取器,不是 LLM 自主理解;
LLM 复审作为可选增强经 hermes_llm 网关接入(见 review.py)。
"""
from __future__ import annotations

from dataclasses import dataclass

from shanghan.corpus import load_entity_dict

_NEGATORS = ("不", "无", "未", "非")


@dataclass(frozen=True)
class EntityMention:
    term: str
    kind: str  # symptom | pulse
    start: int
    end: int
    negated: bool
    optional: bool = False  # 或然证(或X者)


class EntityExtractor:
    def __init__(self, entity_dict: dict | None = None):
        data = entity_dict or load_entity_dict()
        self._terms: list[tuple[str, str]] = sorted(
            [(t, "symptom") for t in data["symptoms"]]
            + [(t, "pulse") for t in data["pulses"]],
            key=lambda x: len(x[0]),
            reverse=True,
        )

    def extract(self, text: str, optional_markers: list[str] | None = None) -> list[EntityMention]:
        optional_set = set(optional_markers or [])
        consumed = [False] * len(text)
        mentions: list[EntityMention] = []
        for term, kind in self._terms:
            start = 0
            while True:
                idx = text.find(term, start)
                if idx == -1:
                    break
                if any(consumed[idx:idx + len(term)]):
                    start = idx + 1
                    continue
                for i in range(idx, idx + len(term)):
                    consumed[i] = True
                negated = idx > 0 and text[idx - 1] in _NEGATORS
                optional = any(term in opt or opt in term for opt in optional_set)
                mentions.append(
                    EntityMention(
                        term=term, kind=kind, start=idx, end=idx + len(term),
                        negated=negated, optional=optional,
                    )
                )
                start = idx + len(term)
        mentions.sort(key=lambda m: m.start)
        return mentions
