"""多评审共识面板(ReviewerAgent / CriticAgent / JudgeAgent)。

确定性评审为基线;LLM 评审经 LLMGateway 注入为额外评委。
共识规则(安全优先): 任一 fail → fail;否则有 warn → warn;否则 pass。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from hermes_llm.gateway import LLMGateway
from hermes_llm.structured import SCHEMAS


@dataclass
class PanelVerdict:
    verdict: str
    problems: list[str] = field(default_factory=list)
    votes: dict[str, str] = field(default_factory=dict)


class ReviewPanel:
    def __init__(self, reviewers: dict[str, Callable[[dict], dict]]):
        """reviewers: 名称 → callable(data) -> {"verdict":..., "problems":[...]}"""
        self._reviewers = reviewers

    @classmethod
    def with_llm(cls, gateway: LLMGateway,
                 base: dict[str, Callable[[dict], dict]] | None = None,
                 roles: tuple[str, ...] = ("tcm_reviewer", "critic")) -> "ReviewPanel":
        reviewers = dict(base or {})
        for role in roles:
            def llm_reviewer(data: dict, _role=role) -> dict:
                model = gateway.structured_call(
                    _role, data, SCHEMAS["ReviewVerdictModel"]
                )
                return model.model_dump()
            reviewers[f"llm:{role}"] = llm_reviewer
        return cls(reviewers)

    @staticmethod
    def _vote(reviewer: Callable[[dict], dict], data: dict) -> tuple[str, dict]:
        try:
            out = reviewer(data)
            return out.get("verdict", "warn"), out
        except Exception as e:
            # 评委故障降级为 warn,不阻断也不放水
            return "warn", {"problems": [f"reviewer_error:{type(e).__name__}"]}

    def review(self, data: dict) -> PanelVerdict:
        votes: dict[str, str] = {}
        problems: list[str] = []
        names = list(self._reviewers)
        # 评审彼此独立(无相互上下文),并行执行;结果按注册顺序合并保持确定性
        if len(names) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
                results = list(
                    pool.map(lambda n: self._vote(self._reviewers[n], data), names)
                )
        else:
            results = [self._vote(self._reviewers[n], data) for n in names]
        for name, (verdict, out) in zip(names, results):
            votes[name] = verdict
            problems.extend(out.get("problems", []))
        if any(v == "fail" for v in votes.values()):
            final = "fail"
        elif any(v == "warn" for v in votes.values()):
            final = "warn"
        else:
            final = "pass"
        return PanelVerdict(verdict=final, problems=problems, votes=votes)
