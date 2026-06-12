"""hermes_llm — LLM 后端抽象(litellm)+ 治理网关。

LLMBackend(litellm/Stub) → LLMGateway(注入隔离 + 剂量出站 + 结构化解析
+ 重试修复 + 成本日志 + 审计哈希链)。
"""
from hermes_llm.backend import (
    BackendUnavailable,
    LiteLLMBackend,
    LLMBackend,
    LLMResult,
    StubLLMBackend,
)
from hermes_llm.gateway import LLMGateway
from hermes_llm.prompts import AgentRoleConfig, PromptRegistry, build_system_prompt

__all__ = [
    "AgentRoleConfig",
    "BackendUnavailable",
    "LLMBackend",
    "LLMGateway",
    "LLMResult",
    "LiteLLMBackend",
    "PromptRegistry",
    "StubLLMBackend",
    "build_system_prompt",
]
