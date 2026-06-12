"""宋本《伤寒论》条文语料加载(公有领域文本;子集标注 PENDING_PHYSICIAN_REVIEW)。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from hermes_contracts.paths import repo_root


@dataclass(frozen=True)
class Clause:
    clause_id: str
    no: int
    channel: str
    section: str
    text: str


def _knowledge_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "knowledge" / "shanghan"


def load_corpus(root: Path | None = None) -> list[Clause]:
    path = _knowledge_dir(root) / "clauses.jsonl"
    clauses = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            clauses.append(Clause(**json.loads(line)))
    return clauses


def corpus_index(root: Path | None = None) -> dict[str, Clause]:
    return {c.clause_id: c for c in load_corpus(root)}


@lru_cache(maxsize=2)
def load_entity_dict(root_str: str = "") -> dict:
    root = Path(root_str) if root_str else None
    path = _knowledge_dir(root) / "entity_dict.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=2)
def load_formula_dict(root_str: str = "") -> dict:
    root = Path(root_str) if root_str else None
    path = _knowledge_dir(root) / "formula_dict.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
