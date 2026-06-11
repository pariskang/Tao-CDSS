#!/usr/bin/env bash
# 任何文件编辑后:若触及安全三模块或 skills/ 规则文件,立即跑安全单测
set -e
CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
if echo "$CHANGED" | grep -qE 'hermes-guard|redflag-engine|audit-chain|red_flags\.yaml|escalation'; then
  .venv/bin/python -m pytest tests/safety -q --maxfail=1 || {
    echo "BLOCKED: 安全单测失败,请先修复再继续" >&2
    exit 2
  }
fi
