"""HermesBroker 医疗 MCP 治理网关(协议 L5)。

坑4: 中间件顺序即安全语义,PIPELINE 顺序由测试锁定,禁止"优化"。
处理顺序: auth → consent → allowlist → schema → injection_scan →
rate_limit → idempotency → execute → egress(PHI最小化+剂量出站) → audit。
audit 记录每次调用(成功或失败)并写入哈希链。
"""
from __future__ import annotations

import re
from collections import defaultdict

from audit_chain import AuditLedger
from hermes_contracts import ConsentFlags, ToolCallEnvelope
from hermes_guard.dose_egress import scan_outbound
from hermes_guard.injection_scanner import scan_payload

#: 顺序即协议,由 tests/unit/test_broker.py 锁定
PIPELINE = (
    "auth",
    "consent",
    "allowlist",
    "schema",
    "injection_scan",
    "rate_limit",
    "idempotency",
    "execute",
    "egress",
    "audit",
)

_WRITE_TOOL = re.compile(r"\.(write|create|update|delete|log)_|\.(write|log)$")

#: 工具 → 所需同意项(协议 L11 三项分离同意)
CONSENT_REQUIRED = {
    "memory.write_episode": "retention",
    "audio.store": "recording",
    "research.export": "research",
}

#: 唯一合法剂量来源(硬规则1),其结构化输出不做剂量出站扫描
DOSE_SOURCE_TOOLS = ("drug_safety.get_dose_range",)

_PHONE = re.compile(r"1[3-9]\d{9}")
_ID_CARD = re.compile(r"\d{17}[\dXx]")


class BrokerError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class Broker:
    PIPELINE = PIPELINE

    def __init__(
        self,
        registry: dict[str, callable],
        ledger: AuditLedger | None = None,
        allowlists: dict[str, set[str]] | None = None,
        rate_limit: int = 100,
    ):
        self._registry = registry
        self._ledger = ledger or AuditLedger()
        self._allowlists = allowlists or {}
        self._rate_limit = rate_limit
        self._idem_cache: dict[str, dict] = {}
        self._counts: dict[str, int] = defaultdict(int)

    def call(
        self,
        env: ToolCallEnvelope,
        consent: ConsentFlags,
        context: dict | None = None,
        _trace: list | None = None,
    ) -> dict:
        trace = _trace if _trace is not None else []
        ctx = context or {}
        outcome: dict = {"status": "ok"}
        injection_hits: list[str] = []
        try:
            self._auth(ctx)
            trace.append("auth")
            self._consent(env, consent)
            trace.append("consent")
            self._allowlist(env)
            trace.append("allowlist")
            self._schema(env)
            trace.append("schema")
            env, injection_hits = self._injection_scan(env)
            trace.append("injection_scan")
            self._rate(env)
            trace.append("rate_limit")
            cached = self._idempotency(env)
            trace.append("idempotency")
            if cached is not None:
                result = cached
                trace.append("execute")  # 幂等命中:返回首次结果,不重复执行
            else:
                result = self._execute(env)
                trace.append("execute")
                if env.idempotency_key:
                    self._idem_cache[env.idempotency_key] = result
            result = self._egress(env, result)
            trace.append("egress")
            outcome["result_keys"] = sorted(result) if isinstance(result, dict) else []
            return result
        except BrokerError as e:
            outcome = {"status": "error", "code": e.status, "detail": e.detail}
            raise
        finally:
            trace.append("audit")
            self._ledger.append(
                env.encounter_id,
                env.agent,
                "tool_call",
                {
                    "tool": env.tool,
                    "skill": env.skill,
                    "tool_call_id": env.tool_call_id,
                    "injection_hits": injection_hits,
                    "outcome": outcome,
                },
            )

    # ------------------------------------------------------------------ stages
    @staticmethod
    def _auth(ctx: dict) -> None:
        if not ctx.get("authenticated"):
            raise BrokerError(401, "未认证(OAuth/院内SSO)")

    @staticmethod
    def _consent(env: ToolCallEnvelope, consent: ConsentFlags) -> None:
        required = CONSENT_REQUIRED.get(env.tool)
        if required and not getattr(consent, required):
            raise BrokerError(403, f"缺少患者同意项: {required}")

    def _allowlist(self, env: ToolCallEnvelope) -> None:
        allowed = self._allowlists.get(env.skill)
        if allowed is not None and env.tool not in allowed:
            raise BrokerError(403, f"工具 {env.tool} 不在 skill {env.skill} 白名单")

    def _schema(self, env: ToolCallEnvelope) -> None:
        if env.tool not in self._registry:
            raise BrokerError(404, f"未注册工具: {env.tool}")
        if not isinstance(env.input, dict):
            raise BrokerError(422, "input 必须为对象")  # pragma: no cover

    @staticmethod
    def _injection_scan(env: ToolCallEnvelope):
        sanitized, hits = scan_payload(env.input)
        if hits:
            env = env.model_copy(update={"input": sanitized})
        return env, hits

    def _rate(self, env: ToolCallEnvelope) -> None:
        self._counts[env.encounter_id] += 1
        if self._counts[env.encounter_id] > self._rate_limit:
            raise BrokerError(429, "超出 encounter 调用预算")

    def _idempotency(self, env: ToolCallEnvelope) -> dict | None:
        if _WRITE_TOOL.search(env.tool):
            if not env.idempotency_key:
                raise BrokerError(422, "写操作必须携带 idempotency_key")
            return self._idem_cache.get(env.idempotency_key)
        return None

    def _execute(self, env: ToolCallEnvelope) -> dict:
        try:
            return self._registry[env.tool](**env.input)
        except BrokerError:
            raise  # pragma: no cover
        except Exception as e:
            raise BrokerError(500, f"工具执行失败: {e}") from e

    def _egress(self, env: ToolCallEnvelope, result: dict) -> dict:
        skip_dose = env.tool in DOSE_SOURCE_TOOLS

        def walk(node):
            if isinstance(node, str):
                node = _PHONE.sub("[手机号已脱敏]", node)
                node = _ID_CARD.sub("[证件号已脱敏]", node)
                if not skip_dose:
                    node = scan_outbound(node).text
                return node
            if isinstance(node, dict):
                return {k: walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v) for v in node]
            return node

        return walk(result)
