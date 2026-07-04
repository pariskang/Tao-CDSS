"""LLMGateway — 所有 LLM 调用的唯一治理出入口。

管线: build_system_prompt(静态) + user data 注入(硬规则4,spotlighting
  定界防注入) → backend.complete(传输层异常指数退避重试)
  → 剂量出站扫描(硬规则1) → JSON 抽取 + schema 校验
  → 失败带具体校验错误重试修复(Retry/Repair Loop)
  → 成本日志 + 审计哈希链;高风险角色可走 k 采样一致性门控。
"""
from __future__ import annotations

import hashlib
import json
import time

from pydantic import BaseModel

from audit_chain import AuditLedger
from hermes_guard.dose_egress import scan_outbound
from hermes_guard.injection_scanner import scan_payload
from hermes_llm.backend import LLMBackend, LLMResult
from hermes_llm.costlog import CostLog
from hermes_llm.prompts import build_system_prompt, get_role
from hermes_llm.structured import ParseFailure, parse_structured


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


#: Spotlighting 定界哨兵(Hines et al. 2024: 显式标记不可信数据边界,
#: 显著降低间接注入成功率)。数据内出现的哨兵字符先被替换为全角变体,
#: 使数据永远无法伪造边界。
DATA_START = "«HERMES_DATA_START»"
DATA_END = "«HERMES_DATA_END»"


def spotlight(payload_json: str) -> str:
    neutral = payload_json.replace("«", "《").replace("»", "》")
    return f"{DATA_START}\n{neutral}\n{DATA_END}"


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
        retry_backoff: float = 0.5,
    ):
        self._backend = backend
        self._ledger = ledger or AuditLedger()
        self._encounter_id = encounter_id
        self.cost_log = cost_log or CostLog()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    # ------------------------------------------------------------------
    def structured_call(
        self, role: str, user_data: dict, schema: type[BaseModel],
        sample_tag: int | None = None,
    ) -> BaseModel:
        config = get_role(role)
        system = build_system_prompt(role)
        # 入站注入检测: 命中即脱敏并审计(检测层;隔离层见 spotlight)
        sanitized, injection_hits = scan_payload(user_data)
        if injection_hits:
            user_data = sanitized
            self._ledger.append(
                self._encounter_id, f"llm:{role}", "injection_detected",
                {"hits": injection_hits, "severity": "WARN"},
            )
        # 不可信内容仅作为 data 注入 user 段(硬规则4);
        # 完整 JSON Schema 一并下发,让模型看到字段级约束而非仅类名。
        envelope: dict = {
            "data": user_data,
            "output_schema": {
                "name": schema.__name__,
                "json_schema": schema.model_json_schema(),
            },
        }
        if sample_tag is not None:  # 一致性采样扰动标记(不污染 data 本体)
            envelope["sample_tag"] = sample_tag
        user = spotlight(json.dumps(envelope, ensure_ascii=False))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error = ""
        for attempt in range(self._max_retries + 1):
            if attempt and self._retry_backoff:
                time.sleep(self._retry_backoff * (2 ** (attempt - 1)))
            try:
                result = self._backend.complete(
                    messages, temperature=config.temperature,
                    max_tokens=config.max_tokens,
                )
            except Exception as e:  # 传输层异常(超时/限流/网络)也纳入重试预算
                last_error = f"backend:{type(e).__name__}: {e}"
                self._audit_transport_failure(role, attempt, last_error)
                continue
            self.cost_log.record(role, result)
            safe_text, dose_violations = self._egress(result)
            self._audit(role, result, messages, dose_violations, attempt)
            try:
                return parse_structured(safe_text, schema)
            except ParseFailure as e:
                last_error = str(e)
                # 修复上下文回注脱敏后文本(避免剂量原文重新进入模型上下文)
                messages = messages + [
                    {"role": "assistant", "content": safe_text[:800]},
                    {"role": "user",
                     "content": f"上一次输出无法解析({last_error})。"
                                "请重新输出: 只输出一个 JSON 对象,严格符合 user 消息"
                                " output_schema.json_schema 的字段与类型,"
                                "不要 markdown 围栏、注释或任何额外文字。"},
                ]
        raise LLMCallFailed(
            f"结构化输出失败(共尝试{self._max_retries + 1}次): {last_error}"
        )

    # ------------------------------------------------------------------
    def structured_call_consistent(
        self, role: str, user_data: dict, schema: type[BaseModel],
        k: int = 3, quorum: int = 2,
    ) -> tuple[BaseModel, dict]:
        """k 采样一致性门控(semantic entropy 的结构化简化,
        Farquhar et al., Nature 2024): 结构化输出天然定义语义等价类
        (canonical JSON 相同 = 同一等价类),k 次独立采样按等价类计票,
        众数达 quorum 才放行;否则视为高不确定性,抛出由调用方走确定性
        兜底。k=1 退化为普通调用(默认零成本增加)。

        返回 (胜出输出, 一致性报告 {samples, agreement, votes, errors})。
        """
        if k <= 1:
            return self.structured_call(role, user_data, schema), {
                "samples": 1, "agreement": 1.0, "votes": 1, "errors": 0,
            }
        buckets: dict[str, list] = {}
        errors = 0
        for i in range(k):
            try:
                out = self.structured_call(
                    role, user_data, schema, sample_tag=i
                )
            except LLMCallFailed:
                errors += 1
                continue
            key = json.dumps(out.model_dump(), sort_keys=True,
                             ensure_ascii=False)
            buckets.setdefault(key, []).append(out)
        if not buckets:
            raise LLMCallFailed(f"一致性采样全部失败({errors}/{k})")
        # 众数等价类;同票取 canonical key 字典序保证确定性
        top_key = max(buckets, key=lambda kk: (len(buckets[kk]), kk))
        top_n = len(buckets[top_key])
        report = {
            "samples": k,
            "agreement": round(top_n / k, 4),
            "votes": top_n,
            "errors": errors,
        }
        self._ledger.append(
            self._encounter_id, f"llm:{role}", "llm_consistency",
            {**report, "equivalence_classes": len(buckets),
             "severity": "INFO" if top_n >= quorum else "WARN"},
        )
        if top_n < quorum:
            raise LLMCallFailed(
                f"一致性不足: 众数 {top_n}/{k} < quorum {quorum}"
                f"(语义等价类 {len(buckets)} 个)"
            )
        return buckets[top_key][0], report

    # ------------------------------------------------------------------
    @staticmethod
    def _egress(result: LLMResult) -> tuple[str, list[str]]:
        """硬规则1: LLM 输出路径上的剂量一律置换并告警。"""
        egress = scan_outbound(result.text)
        return egress.text, [v.text for v in egress.violations]

    def _audit_transport_failure(self, role: str, attempt: int, error: str) -> None:
        self._ledger.append(
            self._encounter_id,
            f"llm:{role}",
            "llm_call_failed",
            {"attempt": attempt, "error": error[:300], "severity": "WARN"},
        )

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
