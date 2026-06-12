"""红旗/升级引擎 — 纯规则实现。

硬规则2: 本包禁止引入任何 LLM 调用或网络外部服务依赖
(tests/safety/test_redflag_engine.py 做静态校验);
E0/E1 升级路径在任何降级层(L0/L1/L2)都必须可用。

规则 YAML 格式:
    rules:
      - id: chest_pain_pressing_diaphoresis
        level: E1
        description: 压榨性胸痛伴出汗/放射
        all_of:
          - any_of: [压榨, 压着痛]
          - any_of: [出汗, 冷汗]
      - id: loss_of_consciousness
        level: E0
        any_of: [意识丧失, 叫不醒]
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SEVERITY = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}

#: 多字否定词:出现在匹配词前 5 字符窗口内即视为被否定
_NEG_MULTI = ("没有", "没得", "否认", "不曾", "未见", "无明显")
#: 单字否定词:必须紧邻匹配词(避免"走不动路还出汗"被误判为否定)
_NEG_SINGLE = ("没", "不", "无", "未")


@dataclass(frozen=True)
class RedFlagHit:
    rule_id: str
    level: str
    matched_terms: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class RedFlagResult:
    level: str | None
    hits: tuple[RedFlagHit, ...]

    def hit_ids(self) -> list[str]:
        return [h.rule_id for h in self.hits]


def _term_present(text: str, term: str) -> bool:
    """词条出现且未被否定。召回优先:否定判定窗口刻意保守。"""
    start = 0
    while True:
        idx = text.find(term, start)
        if idx == -1:
            return False
        window = text[max(0, idx - 5):idx]
        negated = window.endswith(_NEG_SINGLE) or any(
            n in window for n in _NEG_MULTI
        )
        if not negated:
            return True
        start = idx + len(term)


class RedFlagEngine:
    def __init__(self, rules: list[dict]):
        self.rules: list[dict] = []
        for rule in rules:
            if "id" not in rule or "level" not in rule:
                raise ValueError(f"红旗规则缺少 id/level: {rule!r}")
            if rule["level"] not in SEVERITY:
                raise ValueError(f"非法升级等级: {rule['level']!r}")
            if "any_of" not in rule and "all_of" not in rule:
                raise ValueError(f"红旗规则缺少 any_of/all_of: {rule['id']}")
            self.rules.append(rule)

    @classmethod
    def from_yaml(cls, *paths: str | Path) -> "RedFlagEngine":
        rules: list[dict] = []
        for p in paths:
            data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
            rules.extend(data.get("rules", []))
        return cls(rules)

    def scan_text(self, text: str) -> RedFlagResult:
        return self.scan_texts([text])

    def scan_texts(self, texts: list[str]) -> RedFlagResult:
        joined = "\n".join(texts)
        hits: list[RedFlagHit] = []
        for rule in self.rules:
            matched = self._match_rule(rule, joined)
            if matched is not None:
                hits.append(
                    RedFlagHit(
                        rule_id=rule["id"],
                        level=rule["level"],
                        matched_terms=tuple(matched),
                        description=rule.get("description", ""),
                    )
                )
        level = None
        if hits:
            level = min((h.level for h in hits), key=lambda lv: SEVERITY[lv])
        return RedFlagResult(level=level, hits=tuple(hits))

    @staticmethod
    def _match_rule(rule: dict, text: str) -> list[str] | None:
        if "any_of" in rule:
            for term in rule["any_of"]:
                if _term_present(text, term):
                    return [term]
            return None
        matched: list[str] = []
        for group in rule["all_of"]:
            terms = group["any_of"] if isinstance(group, dict) else [group]
            hit = next((t for t in terms if _term_present(text, t)), None)
            if hit is None:
                return None
            matched.append(hit)
        return matched
