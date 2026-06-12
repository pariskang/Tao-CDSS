"""LLM 成本日志(协议 L10 模型治理的一部分)。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostEntry:
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass
class CostLog:
    entries: list[CostEntry] = field(default_factory=list)

    def record(self, role: str, result) -> None:
        self.entries.append(
            CostEntry(
                role=role,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
            )
        )

    def summary(self) -> dict:
        return {
            "calls": len(self.entries),
            "prompt_tokens": sum(e.prompt_tokens for e in self.entries),
            "completion_tokens": sum(e.completion_tokens for e in self.entries),
            "cost_usd": round(sum(e.cost_usd for e in self.entries), 6),
            "by_role": {
                role: sum(1 for e in self.entries if e.role == role)
                for role in sorted({e.role for e in self.entries})
            },
        }
