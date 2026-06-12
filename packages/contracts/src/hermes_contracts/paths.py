"""仓库根定位工具:knowledge/、skills/ 等资源的统一锚点。"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("HERMES_ROOT")
    if env:
        return Path(env)
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("repo root not found")  # pragma: no cover
