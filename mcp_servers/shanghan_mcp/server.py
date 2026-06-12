"""Shanghan-Hermes MCP server(stdio, newline-delimited JSON-RPC 2.0)。

零第三方依赖的最小 MCP 实现,供 Claude Code / Codex / OpenClaw 等
支持 MCP 的编码智能体接入(配置见 .mcp.json 与 integrations/)。
生产部署按协议 L5 走 Streamable HTTP + HermesBroker 网关。
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "shanghan-hermes", "version": "2.0.0"}

def _ensure_pipeline():
    from shanghan.runtime import default_rag, default_result

    return default_result(), default_rag()


# ---------------------------------------------------------------- tools
def tool_shanghan_ask(question: str, role: str = "doctor") -> dict:
    _, rag = _ensure_pipeline()
    return rag.ask(question, role=role)


def tool_shanghan_match(symptoms: list[str], pulses: list[str] | None = None) -> dict:
    result, _ = _ensure_pipeline()
    from shanghan.matcher import FormulaMatcher

    matches = FormulaMatcher(result.patterns).match(symptoms, pulses or [])
    return {
        "matches": [
            {"formula": m.formula, "score": m.score, "matched_core": m.matched_core,
             "missing_core": m.missing_core,
             "supporting_clauses": m.supporting_clauses}
            for m in matches
        ],
        "notice": "仅供执业医师参考,需四诊合参",
    }


def tool_shanghan_stats() -> dict:
    result, _ = _ensure_pipeline()
    return result.stats


def tool_nrs_pain(score: int) -> dict:
    from mcp_servers.calculator.server import nrs_pain

    return nrs_pain(score)


TOOLS = {
    "shanghan_ask": {
        "fn": tool_shanghan_ask,
        "description": "向伤寒论知识系统提问(自动路由: 方证匹配/鉴别/禁忌/误治/条文/六经/科普)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "role": {"type": "string", "enum": ["doctor", "researcher", "patient"],
                          "default": "doctor"},
            },
            "required": ["question"],
        },
    },
    "shanghan_match": {
        "fn": tool_shanghan_match,
        "description": "症状/脉象 → 方证候选(医生端)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symptoms": {"type": "array", "items": {"type": "string"}},
                "pulses": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["symptoms"],
        },
    },
    "shanghan_stats": {
        "fn": tool_shanghan_stats,
        "description": "规则挖掘管线统计(规则数/置信分层/方证数)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "nrs_pain": {
        "fn": tool_nrs_pain,
        "description": "NRS 疼痛评分分级(确定性计算)",
        "inputSchema": {
            "type": "object",
            "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 10}},
            "required": ["score"],
        },
    },
}


# ---------------------------------------------------------------- JSON-RPC
def handle(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return _ok(req_id, {
            "tools": [
                {"name": name, "description": t["description"],
                 "inputSchema": t["inputSchema"]}
                for name, t in TOOLS.items()
            ]
        })
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        if name not in TOOLS:
            return _err(req_id, -32602, f"unknown tool: {name}")
        try:
            result = TOOLS[name]["fn"](**params.get("arguments", {}))
            return _ok(req_id, {
                "content": [{"type": "text",
                             "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            })
        except Exception as e:
            return _ok(req_id, {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True,
            })
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def main() -> None:  # pragma: no cover - 由集成测试经子进程覆盖
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    main()
