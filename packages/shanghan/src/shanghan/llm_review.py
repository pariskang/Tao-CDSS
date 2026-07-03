"""LLM 多评审接入 — 把 hermes_llm 网关适配为 extra_reviewers。

docs 架构图声明的"可选 LLM 多评审(ReviewPanel)"此前只是预置注入点,
本模块使其可真实接线:
  - make_llm_reviewers(gateway): 显式注入(测试/私有化部署);
  - default_llm_reviewers(): 环境配置了 HERMES_LLM_MODEL 时自动装配,
    否则返回空列表,确定性评审基线不变。
安全边界(硬规则7): 所有调用经 LLMGateway(剂量出站+审计);
评审故障降级为 warn,不阻断也不放水;共识规则不变——任一 fail 即 fail,
ReleaseGate 硬拒绝条件不因 LLM 评审通过而放宽。
"""
from __future__ import annotations

import os
from typing import Callable

from shanghan.review import ReviewVerdict
from shanghan.rules import InitialRule

_VALID = ("pass", "warn", "fail")


def make_llm_reviewers(
    gateway, roles: tuple[str, ...] = ("tcm_reviewer", "critic")
) -> list[Callable[[InitialRule], ReviewVerdict]]:
    from hermes_llm.structured import SCHEMAS

    def build(role: str) -> Callable[[InitialRule], ReviewVerdict]:
        def reviewer(rule: InitialRule) -> ReviewVerdict:
            data = {
                "rule_id": rule.rule_id,
                "rule_type": rule.rule_type,
                "if_conditions": rule.if_conditions,
                "then_conclusions": rule.then_conclusions,
                "evidence_span": rule.evidence_span,
                "condition_span": rule.condition_span,
                "conclusion_span": rule.conclusion_span,
            }
            try:
                out = gateway.structured_call(
                    role, data, SCHEMAS["ReviewVerdictModel"]
                )
                verdict = out.verdict if out.verdict in _VALID else "warn"
                return ReviewVerdict(
                    verdict, [f"llm:{role}:{p}" for p in out.problems]
                )
            except Exception as e:
                # 评审故障降级 warn: 不阻断确定性基线,也不视为通过
                return ReviewVerdict(
                    "warn", [f"llm_reviewer_error:{role}:{type(e).__name__}"]
                )

        return reviewer

    return [build(r) for r in roles]


def default_llm_reviewers() -> list[Callable[[InitialRule], ReviewVerdict]]:
    """HERMES_LLM_MODEL 已配置 → 装配 LLM 评审;否则空列表(纯确定性)。"""
    if not os.environ.get("HERMES_LLM_MODEL"):
        return []
    try:
        from hermes_llm import LiteLLMBackend, LLMGateway

        backend = LiteLLMBackend()
        gateway = LLMGateway(backend, encounter_id="shanghan_review")
        return make_llm_reviewers(gateway)
    except Exception:
        # 后端不可用时静默回落确定性基线(与 agents 层降级语义一致)
        return []
