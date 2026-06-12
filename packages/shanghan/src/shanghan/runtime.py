"""共享运行时缓存: 管线结果与 SkillRAG 单例(供 agents / MCP server 复用)。"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def default_result():
    from shanghan.pipeline import ShanghanPipeline

    return ShanghanPipeline().run()


@lru_cache(maxsize=1)
def default_rag():
    from shanghan.skill_rag import SkillRAG

    return SkillRAG(default_result())
