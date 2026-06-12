"""方言症状俗称消歧(协议 L1.2 dialect_lexicon):一对多映射,歧义必澄清不自动选择。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hermes_contracts.paths import repo_root


@dataclass(frozen=True)
class DialectMapping:
    term: str
    candidates: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


class DialectMapper:
    def __init__(self, lexicon: dict[str, list[str]]):
        self._lexicon = lexicon

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "DialectMapper":
        p = (
            Path(path)
            if path
            else repo_root() / "knowledge" / "dialect_symptom_lexicon.yaml"
        )
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return cls(data.get("lexicon", {}))

    def find(self, text: str) -> list[DialectMapping]:
        found: list[DialectMapping] = []
        remaining = text
        for term in sorted(self._lexicon, key=len, reverse=True):
            if term in remaining:
                found.append(
                    DialectMapping(term=term, candidates=tuple(self._lexicon[term]))
                )
                remaining = remaining.replace(term, "□" * len(term))
        return found
