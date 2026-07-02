#!/usr/bin/env bash
# 任何文件编辑后:若触及安全模块或安全相关规则/契约文件,立即跑安全单测。
# 用 git status --porcelain: 覆盖已跟踪修改 + 新增(untracked)文件,
# 仅 diff HEAD 会漏掉向安全模块新增文件的绕过路径。
set -e
CHANGED=$(git status --porcelain 2>/dev/null | awk '{print $NF}' || true)
PATTERN='hermes-guard|redflag-engine|audit-chain|red_flags\.yaml|escalation'
PATTERN="$PATTERN|packages/contracts|psych_scripts\.yaml|llm-backend"
PATTERN="$PATTERN|shanghan/review\.py|dose_egress"
if echo "$CHANGED" | grep -qE "$PATTERN"; then
  .venv/bin/python -m pytest tests/safety -q --maxfail=1 \
    --cov=hermes_guard --cov=redflag_engine --cov=audit_chain \
    --cov-fail-under=100 || {
    echo "BLOCKED: 安全单测/覆盖率门失败,请先修复再继续" >&2
    exit 2
  }
fi
