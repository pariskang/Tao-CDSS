"""注入扫描器(协议 L8.5)。

患者转写与工具返回永远作为不可信 data;命中注入模式即脱敏标注,
不拼接 system 区(硬规则4 由 build_system_prompt 调用点保证)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SANITIZED_MARK = "[可疑指令已脱敏]"

_PATTERNS = [
    r"忽略(以上|之前|前面|所有)?的?(指令|提示|规则)",
    r"无视(以上|之前)?的?(规则|指令|限制)",
    r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"你现在是(?!患者)[^,。;\n]{0,12}",
    r"扮演[^,。;\n]{0,6}(医生|管理员|系统)",
    r"system\s*prompt",
    r"developer\s+mode",
    r"越狱",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


@dataclass
class InjectionScanResult:
    detected: bool
    sanitized_text: str
    patterns_hit: list[str] = field(default_factory=list)


def scan(text: str) -> InjectionScanResult:
    hits: list[str] = []
    sanitized = text
    for pat in _COMPILED:
        if pat.search(sanitized):
            hits.append(pat.pattern)
            sanitized = pat.sub(SANITIZED_MARK, sanitized)
    return InjectionScanResult(
        detected=bool(hits), sanitized_text=sanitized, patterns_hit=hits
    )


def scan_payload(obj):
    """递归扫描 dict/list/str,返回 (脱敏后对象, 命中模式列表)。"""
    hits: list[str] = []

    def walk(node):
        if isinstance(node, str):
            res = scan(node)
            hits.extend(res.patterns_hit)
            return res.sanitized_text
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(obj), hits
