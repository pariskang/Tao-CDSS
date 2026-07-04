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
    metrics: dict = field(default_factory=dict)  # 验收指标(评测门禁消费)
    #: (slot, condition) → 似然比,供信息增益选问;临床数值 PENDING 医师审定
    lr_table: dict = field(default_factory=dict)

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
            cost=float(s.get("cost", 1.0)),
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
    # 加载期一致性校验: discriminator 必须是已定义槽位,否则回补路径
    # "有未闭环判别项但无可问槽位"会让引擎卡死——配置错误必须在加载期暴露
    slot_names = {s.name for s in slots}
    for m in mnm:
        unknown = [d for d in m.discriminators if d not in slot_names]
        if unknown:
            raise ValueError(
                f"skill {name}: must_not_miss[{m.condition}] 的判别项 "
                f"{unknown} 不在 slots.yaml 定义的槽位中"
            )

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

    metrics: dict = {}
    metrics_path = base / "metrics.yaml"
    if metrics_path.exists():
        metrics = yaml.safe_load(
            metrics_path.read_text(encoding="utf-8")
        ) or {}

    # 似然比表(可选): slots.yaml 顶层 lr_table 条目
    # [{slot, condition, lr}],激活价值驱动选问的信息增益分支
    lr_table: dict = {}
    for entry in slots_data.get("lr_table", []):
        if entry["slot"] not in slot_names:
            raise ValueError(
                f"skill {name}: lr_table 引用未定义槽位 {entry['slot']}"
            )
        lr_table[(entry["slot"], entry["condition"])] = float(entry["lr"])

    return Skill(
        name=name,
        path=base,
        slots=slots,
        red_flag_rules=red_flag_rules,
        must_not_miss=mnm,
        tools_allow=tools_allow,
        metrics=metrics,
        lr_table=lr_table,
    )
