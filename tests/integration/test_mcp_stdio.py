"""MCP stdio server 集成测试: initialize → tools/list → tools/call。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_contracts.paths import repo_root

PYPATH = ":".join([
    ".",
    "packages/contracts/src", "packages/redflag-engine/src",
    "packages/audit-chain/src", "packages/eventstore/src",
    "packages/shanghan/src", "packages/llm-backend/src",
    "services/hermes-guard/src", "services/hermes-loop/src",
    "services/hermes-broker/src", "services/hermes-compose/src",
    "services/hermes-voice/src", "services/hermes-agents/src",
])


@pytest.fixture(scope="module")
def mcp_responses():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "nrs_pain", "arguments": {"score": 8}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "shanghan_match",
                    "arguments": {"symptoms": ["项背强几几", "无汗"]}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "ghost_tool", "arguments": {}}},
    ]
    stdin = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_servers.shanghan_mcp.server"],
        input=stdin, capture_output=True, text=True, timeout=120,
        cwd=repo_root(), env={"PYTHONPATH": PYPATH, "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    return {
        r["id"]: r
        for r in (json.loads(line) for line in proc.stdout.splitlines() if line)
    }


def test_initialize(mcp_responses):
    result = mcp_responses[1]["result"]
    assert result["serverInfo"]["name"] == "shanghan-hermes"
    assert "tools" in result["capabilities"]


def test_tools_list(mcp_responses):
    tools = {t["name"] for t in mcp_responses[2]["result"]["tools"]}
    assert {"shanghan_ask", "shanghan_match", "shanghan_stats", "nrs_pain"} <= tools


def test_tool_call_calculator(mcp_responses):
    content = json.loads(mcp_responses[3]["result"]["content"][0]["text"])
    assert content["band"] == "severe"


def test_tool_call_shanghan_match(mcp_responses):
    content = json.loads(mcp_responses[4]["result"]["content"][0]["text"])
    assert content["matches"][0]["formula"] == "葛根汤"


def test_unknown_tool_error(mcp_responses):
    assert "error" in mcp_responses[5]
