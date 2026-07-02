"""mcp-triage-server — 仅包装 packages/redflag-engine;
server 挂掉时 loop 内嵌引擎兜底(协议 L5)。"""
from __future__ import annotations

from functools import lru_cache

from hermes_contracts.paths import repo_root
from redflag_engine import RedFlagEngine


@lru_cache(maxsize=1)
def _engine() -> RedFlagEngine:
    return RedFlagEngine.from_yaml(repo_root() / "knowledge" / "red_flags" / "global.yaml")


def red_flag_check(texts: list[str] | str) -> dict:
    # 容错: 传入单个字符串时包装为列表。list("字符串") 会拆成单字,
    # 使红旗关键词永不匹配——安全兜底路径上的静默假阴性,必须拦住。
    if isinstance(texts, str):
        texts = [texts]
    if not isinstance(texts, (list, tuple)) or not all(
        isinstance(t, str) for t in texts
    ):
        raise TypeError("texts 必须是 str 或 list[str]")
    result = _engine().scan_texts(list(texts))
    return {
        "level": result.level,
        "hits": [
            {
                "rule_id": h.rule_id,
                "level": h.level,
                "matched_terms": list(h.matched_terms),
            }
            for h in result.hits
        ],
    }


TOOLS = {"triage.red_flag_check": red_flag_check}
