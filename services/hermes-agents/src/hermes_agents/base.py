"""Agent 基类: LLM 增强 + 确定性 fallback(LLM 失败绝不阻断)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from hermes_llm.gateway import LLMGateway


@dataclass
class AgentResult:
    agent: str
    output: dict
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)


class BaseAgent:
    name: str = "base"
    role: str = ""  # PromptRegistry 角色名;空 = 纯确定性 Agent

    def __init__(self, gateway: LLMGateway | None = None):
        self._gateway = gateway

    def run(self, task: dict) -> AgentResult:
        if self._gateway is not None and self.role:
            try:
                output = self._llm_run(task)
                return AgentResult(agent=self.name, output=output, used_llm=True)
            except Exception as e:  # LLM 失败 → 确定性兜底
                fallback = self.fallback(task)
                return AgentResult(
                    agent=self.name, output=fallback, used_llm=False,
                    notes=[f"llm_fallback:{type(e).__name__}"],
                )
        return AgentResult(agent=self.name, output=self.fallback(task))

    # 子类实现 ----------------------------------------------------------
    def _llm_run(self, task: dict) -> dict:
        raise NotImplementedError  # pragma: no cover

    def fallback(self, task: dict) -> dict:
        raise NotImplementedError  # pragma: no cover
