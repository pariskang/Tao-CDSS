"""mcp-terminology-server — 症状/药名归一(本地词表 + 近音混淆对照)。"""
from __future__ import annotations

from functools import lru_cache

import yaml

from hermes_contracts.paths import repo_root


@lru_cache(maxsize=1)
def _lexicon() -> dict:
    p = repo_root() / "knowledge" / "dialect_symptom_lexicon.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")).get("lexicon", {})


@lru_cache(maxsize=1)
def _confusion_pairs() -> dict:
    p = repo_root() / "knowledge" / "drug_confusion_pairs.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")).get("pairs", {})


def normalize_symptom(text: str, dialect_region: str | None = None) -> dict:
    lex = _lexicon()
    matches = []
    remaining = text
    for term in sorted(lex, key=len, reverse=True):
        if term in remaining:
            candidates = lex[term]
            matches.append(
                {
                    "term": term,
                    "candidates": list(candidates),
                    "ambiguous": len(candidates) > 1,
                    "concept_id": candidates[0] if len(candidates) == 1 else None,
                }
            )
            remaining = remaining.replace(term, "□" * len(term))
    return {"text": text, "dialect_region": dialect_region, "matches": matches}


def map_drug_name(text: str) -> dict:
    pairs = _confusion_pairs()
    return {
        "text": text,
        "drug_id": f"local:{text}" if text in pairs or text else None,
        "confusion_pairs": list(pairs.get(text, [])),
        "requires_double_confirm": text in pairs,
    }


TOOLS = {
    "terminology.normalize_symptom": normalize_symptom,
    "terminology.map_drug_name": map_drug_name,
}
