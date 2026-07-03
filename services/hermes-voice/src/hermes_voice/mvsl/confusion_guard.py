"""近音药名双确认(协议 L1.2 critical_policy)。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hermes_contracts.paths import repo_root


@dataclass(frozen=True)
class ConfirmRequest:
    kind: str
    term: str
    prompt: str
    alternatives: tuple[str, ...] = ()


class ConfusionGuard:
    def __init__(self, pairs: dict[str, list[str]]):
        self._pairs = pairs

    @property
    def terms(self) -> tuple[str, ...]:
        """混淆对照表中的全部药名(供调用方扫描文本命中,长词优先)。"""
        return tuple(sorted(self._pairs, key=len, reverse=True))

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "ConfusionGuard":
        p = Path(path) if path else repo_root() / "knowledge" / "drug_confusion_pairs.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return cls(data.get("pairs", {}))

    def check(self, drug_text: str, asr_confidence: float = 1.0) -> ConfirmRequest | None:
        alternatives = tuple(self._pairs.get(drug_text, ()))
        if alternatives:
            alts = "、".join(f"「{a}」" for a in alternatives)
            return ConfirmRequest(
                kind="drug_name",
                term=drug_text,
                prompt=f"我和您确认一下,您说的是「{drug_text}」,不是{alts},对吗?",
                alternatives=alternatives,
            )
        if asr_confidence < 0.90:
            return ConfirmRequest(
                kind="drug_name",
                term=drug_text,
                prompt=f"我没有完全听清,您说的药名是「{drug_text}」吗?",
            )
        return None
