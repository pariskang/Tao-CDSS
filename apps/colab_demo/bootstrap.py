"""把 monorepo 的全部包路径注入 sys.path(Colab/脚本运行用,等价 justfile)。"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIRS = (
    ".",
    "packages/contracts/src",
    "packages/redflag-engine/src",
    "packages/audit-chain/src",
    "packages/eventstore/src",
    "packages/shanghan/src",
    "packages/llm-backend/src",
    "services/hermes-guard/src",
    "services/hermes-loop/src",
    "services/hermes-broker/src",
    "services/hermes-compose/src",
    "services/hermes-voice/src",
    "services/hermes-agents/src",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def setup_paths() -> Path:
    root = repo_root()
    for rel in _SRC_DIRS:
        p = str(root / rel)
        if p not in sys.path:
            sys.path.insert(0, p)
    return root
