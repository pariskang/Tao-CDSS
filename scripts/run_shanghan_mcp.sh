#!/usr/bin/env bash
# Shanghan-Hermes MCP server 启动器(stdio)
# 供 Claude Code(.mcp.json)/ Codex / OpenClaw 接入
cd "$(dirname "$0")/.."
export PYTHONPATH=".:packages/contracts/src:packages/redflag-engine/src:packages/audit-chain/src:packages/eventstore/src:packages/shanghan/src:packages/llm-backend/src:services/hermes-guard/src:services/hermes-loop/src:services/hermes-broker/src:services/hermes-compose/src:services/hermes-voice/src:services/hermes-agents/src"
exec .venv/bin/python -m mcp_servers.shanghan_mcp.server
