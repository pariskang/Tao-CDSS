"""共享运行时缓存: 管线结果与 SkillRAG 单例(供 agents / MCP server 复用)。"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def default_result():
    from shanghan.llm_review import default_llm_reviewers
    from shanghan.pipeline import ShanghanPipeline

    # HERMES_LLM_MODEL 已配置 → LLM 多评审自动接线;未配置纯确定性
    return ShanghanPipeline(
        extra_reviewers=default_llm_reviewers() or None
    ).run()


@lru_cache(maxsize=1)
def default_rag():
    from shanghan.skill_rag import SkillRAG

    return SkillRAG(default_result())
