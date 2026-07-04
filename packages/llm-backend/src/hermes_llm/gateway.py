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


#: Spotlighting 定界哨兵(Hines et al. 2024, arXiv:2403.14720: 显式标记
#: 不可信数据边界,显著降低间接注入成功率)。数据内出现的哨兵字符先被
#: 替换为全角变体,使数据永远无法伪造边界。
DATA_START = "«HERMES_DATA_START»"
DATA_END = "«HERMES_DATA_END»"
#: Datamarking 标记符(U+2063 INVISIBLE SEPARATOR): Hines et al. 的
#: datamarking 在英文用特殊字符替换空格;中文无空格,等价形式是在数据
#: 字符串叶的字符间插入标记——注入指令("忽略以上指令")在 prompt 中
#: 不再以连续子串出现,而 system 守则已声明该标记 = 不可信数据。
DATAMARK = "⁣"


def spotlight(payload_json: str) -> str:
    neutral = payload_json.replace("«", "《").replace("»", "》")
    return f"{DATA_START}\n{neutral}\n{DATA_END}"


def datamark_tree(value):
    """递归对数据字符串叶做 datamarking;dict 的 key 不动(结构可读)。

    先剥离叶内既有标记符——攻击者预置 DATAMARK 无法伪装成"已标记"
    或干扰标记密度。仅处理 str/list/tuple/dict 叶,数值布尔原样。
    """
    if isinstance(value, str):
        clean = value.replace(DATAMARK, "")
        return DATAMARK.join(clean) if clean else clean
    if isinstance(value, list):
        return [datamark_tree(x) for x in value]
    if isinstance(value, tuple):
        return tuple(datamark_tree(x) for x in value)
    if isinstance(value, dict):
        return {k: datamark_tree(v) for k, v in value.items()}
    return value


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
        datamark: bool = True,
    ):
        self._backend = backend
        self._ledger = ledger or AuditLedger()
        self._encounter_id = encounter_id
        self.cost_log = cost_log or CostLog()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        # datamarking 使数据 token 量增加(CJK 约 2 倍),对强模型的理解
        # 影响有限(Hines et al. 报告任务性能基本保持);可按部署关闭
        self._datamark = datamark

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
            "data": datamark_tree(user_data) if self._datamark else user_data,
            "output_schema": {
                "name": schema.__name__,
                "json_schema": schema.model_json_schema(),
            },
        }
        if sample_tag is not None:  # 一致性采样扰动标记(不污染 data 本体)
            envelope["sample_tag"] = sample_tag
        user = spotlight(json.dumps(envelope, ensure_ascii=False))
        # 边界不变量(纵深防御): 最终 prompt 有且仅有一对数据哨兵,
        # 任何伪造边界都已被中和;违反即拒绝并审计
        if user.count(DATA_START) != 1 or user.count(DATA_END) != 1:
            self._ledger.append(
                self._encounter_id, f"llm:{role}", "spotlight_violation",
                {"severity": "CRIT"},
            )
            raise LLMCallFailed("spotlight 边界不变量被破坏,拒绝外呼")
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
        safety_fields: tuple[str, ...] = (),
        info_fields: tuple[str, ...] = (),
    ) -> tuple[BaseModel, dict]:
        """k 采样字段级一致性门控(semantic entropy 的结构化简化,
        Farquhar et al., Nature 2024;字段级计票见 consistency_gate)。

        k 次独立采样 → 按 Pydantic 字段分档计票(safety 全票/standard
        法定多数/info 仅记录) → 达标返回 medoid 真实样本,否则抛出
        由调用方走确定性兜底。k=1 退化为普通调用(默认零成本)。
        门控只有否决权: 放行结果仍过 dose_egress 等全部下游校验。

        返回 (medoid 样本, 报告 {samples, agreement, votes, errors, fields})。
        """
        from hermes_llm.consistency_gate import FAIL, PASS, gate

        if k <= 1:
            return self.structured_call(role, user_data, schema), {
                "samples": 1, "agreement": 1.0, "votes": 1, "errors": 0,
            }
        samples: list[BaseModel] = []
        errors = 0
        for i in range(k):
            try:
                samples.append(
                    self.structured_call(role, user_data, schema, sample_tag=i)
                )
            except LLMCallFailed:
                errors += 1
        if not samples:
            raise LLMCallFailed(f"一致性采样全部失败({errors}/{k})")
        quorum_ratio = quorum / k
        result = gate(
            samples, safety_fields=safety_fields, info_fields=info_fields,
            quorum_ratio=quorum_ratio,
        )
        votes = min(
            (f["modal_votes"] for f in result.field_report.values()
             if f["tier"] != "info"),
            default=len(samples),
        )
        report = {
            "samples": k,
            "agreement": result.min_agreement,
            "votes": votes,
            "errors": errors,
            "fields": result.field_report,
        }
        self._ledger.append(
            self._encounter_id, f"llm:{role}", "llm_consistency",
            {"samples": k, "agreement": result.min_agreement,
             "decision": result.decision, "errors": errors,
             "severity": "INFO" if result.decision == PASS else "WARN"},
        )
        if result.decision != PASS:
            detail = "安全字段分歧" if result.decision == FAIL else (
                f"众数 {votes}/{k} < quorum {quorum}"
            )
            raise LLMCallFailed(f"一致性不足: {detail}")
        return samples[result.chosen_index], report

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
