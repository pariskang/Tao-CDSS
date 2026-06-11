"""临床能力包(Skill)加载器(协议 L4)。

skill 包七件套: SKILL.md / schema.json / slots.yaml / red_flags.yaml /
tools.allow / eval_cases.jsonl / metrics.yaml(+ fallback.yaml)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hermes_contracts.paths import repo_root
from hermes_loop.question_planner import Slot


@dataclass
class MustNotMiss:
    condition: str
    discriminators: list[str]
    probability: float = 0.05


@dataclass
class Skill:
    name: str
    path: Path
    slots: list[Slot] = field(default_factory=list)
    red_flag_rules: list[dict] = field(default_factory=list)
    must_not_miss: list[MustNotMiss] = field(default_factory=list)
    tools_allow: list[str] = field(default_factory=list)

    @property
    def eval_cases_path(self) -> Path:
        return self.path / "eval_cases.jsonl"


def load_skill(name: str, root: Path | None = None) -> Skill:
    base = (root or repo_root()) / "skills" / name
    if not base.is_dir():
        raise FileNotFoundError(f"skill 不存在: {base}")

    slots_data = yaml.safe_load((base / "slots.yaml").read_text(encoding="utf-8"))
    slots = [
        Slot(
            name=s["name"],
            question=s["question"],
            priority=s.get("priority", 100),
            required=s.get("required", False),
            red_flag=s.get("red_flag", False),
            sensitive=s.get("sensitive", False),
            reason=s.get("reason", ""),
            keywords=tuple(s.get("keywords", [])),
        )
        for s in slots_data.get("slots", [])
    ]
    mnm = [
        MustNotMiss(
            condition=m["condition"],
            discriminators=list(m.get("discriminators", [])),
            probability=m.get("probability", 0.05),
        )
        for m in slots_data.get("must_not_miss", [])
    ]

    red_flag_rules: list[dict] = []
    rf_path = base / "red_flags.yaml"
    if rf_path.exists():
        red_flag_rules = (
            yaml.safe_load(rf_path.read_text(encoding="utf-8")) or {}
        ).get("rules", [])

    tools_allow: list[str] = []
    allow_path = base / "tools.allow"
    if allow_path.exists():
        tools_allow = [
            line.strip()
            for line in allow_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    return Skill(
        name=name,
        path=base,
        slots=slots,
        red_flag_rules=red_flag_rules,
        must_not_miss=mnm,
        tools_allow=tools_allow,
    )
