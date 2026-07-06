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


@lru_cache(maxsize=2)
def _load_corpus_cached(path: Path) -> tuple[Clause, ...]:
    clauses = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            clauses.append(Clause(**json.loads(line)))
    return tuple(clauses)


def load_corpus(root: Path | None = None) -> list[Clause]:
    """语料进程级缓存(管线 + SkillRAG + MCP 共用,避免重复读盘解析)。
    返回新 list,调用方可安全持有/排序,底层 Clause 为 frozen dataclass。"""
    return list(_load_corpus_cached(_knowledge_dir(root) / "clauses.jsonl"))


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
