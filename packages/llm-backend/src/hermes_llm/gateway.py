"""LLMGateway — 所有 LLM 调用的唯一治理出入口。

管线: build_system_prompt(静态) + user data 注入(硬规则4)
  → backend.complete → 剂量出站扫描(硬规则1) → JSON 抽取 + schema 校验
  → 失败带错误提示重试(Retry/Repair Loop) → 成本日志 + 审计哈希链。
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from audit_chain import AuditLedger
from hermes_guard.dose_egress import scan_outbound
from hermes_llm.backend import LLMBackend, LLMResult
from hermes_llm.costlog import CostLog
from hermes_llm.prompts import build_system_prompt, get_role
from hermes_llm.structured import ParseFailure, parse_structured


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class LLMCallFailed(RuntimeError):
    pass


class LLMGateway:
    def __init__(
        self,
        backend: LLMBackend,
        ledger: AuditLedger | None = None,
        encounter_id: str = "llm_session",
        cost_log: CostLog | None = None,
        max_retries: int = 2,
    ):
        self._backend = backend
        self._ledger = ledger or AuditLedger()
        self._encounter_id = encounter_id
        self.cost_log = cost_log or CostLog()
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    def structured_call(
        self, role: str, user_data: dict, schema: type[BaseModel]
    ) -> BaseModel:
        config = get_role(role)
        system = build_system_prompt(role)
        # 不可信内容仅作为 data 注入 user 段(硬规则4)
        user = json.dumps(
            {"data": user_data, "output_schema": schema.__name__},
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error = ""
        for attempt in range(self._max_retries + 1):
            result = self._backend.complete(
                messages, temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
            self.cost_log.record(role, result)
            safe_text, dose_violations = self._egress(result)
            self._audit(role, result, messages, dose_violations, attempt)
            try:
                return parse_structured(safe_text, schema)
            except ParseFailure as e:
                last_error = str(e)
                messages = messages + [
                    {"role": "assistant", "content": result.text},
                    {"role": "user",
                     "content": f"上一次输出无法解析({last_error})。"
                                f"请只输出符合 {schema.__name__} 的 JSON 对象。"},
                ]
        raise LLMCallFailed(f"结构化输出失败(重试{self._max_retries}次): {last_error}")

    # ------------------------------------------------------------------
    @staticmethod
    def _egress(result: LLMResult) -> tuple[str, list[str]]:
        """硬规则1: LLM 输出路径上的剂量一律置换并告警。"""
        egress = scan_outbound(result.text)
        return egress.text, [v.text for v in egress.violations]

    def _audit(self, role, result, messages, dose_violations, attempt) -> None:
        self._ledger.append(
            self._encounter_id,
            f"llm:{role}",
            "llm_call",
            {
                "model": result.model,
                "attempt": attempt,
                "prompt_digest": _digest(json.dumps(messages, ensure_ascii=False)),
                "response_digest": _digest(result.text),
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "cost_usd": result.cost_usd,
                "dose_violations": dose_violations,
                "severity": "CRIT" if dose_violations else "INFO",
            },
        )
