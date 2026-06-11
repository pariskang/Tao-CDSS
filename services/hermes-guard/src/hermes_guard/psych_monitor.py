"""精神科风险旁路监测(协议 L8.3)。

硬规则6: 触发后话术只能来自 knowledge/psych_scripts.yaml 固定文案,
禁止改为 LLM 生成;相关内容仅入审计账本,禁入任何长期记忆。
召回优先:不做否定排除("我不会自杀"仍触发人工通道)。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from hermes_contracts.paths import repo_root

_SELF_HARM_KEYWORDS = (
    "自杀",
    "想死",
    "不想活",
    "活不下去",
    "活着没意思",
    "自残",
    "割腕",
    "轻生",
    "结束生命",
    "结束自己的生命",
    "伤害自己",
    "跳楼",
)
_HARM_OTHERS_KEYWORDS = ("杀了他", "杀了她", "伤害别人", "同归于尽")


def triggered(text: str) -> bool:
    return any(k in text for k in _SELF_HARM_KEYWORDS + _HARM_OTHERS_KEYWORDS)


def detect_kind(text: str) -> str:
    if any(k in text for k in _HARM_OTHERS_KEYWORDS):
        return "harm_others"
    return "self_harm"


@lru_cache(maxsize=4)
def load_scripts(path: str | None = None) -> dict:
    p = Path(path) if path else repo_root() / "knowledge" / "psych_scripts.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def get_script(kind: str = "self_harm", path: str | None = None) -> dict:
    scripts = load_scripts(path)
    if kind not in scripts:
        raise KeyError(f"未定义的心理危机话术类型: {kind}")
    return scripts[kind]
