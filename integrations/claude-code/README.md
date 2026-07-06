# Claude Code 接入

仓库根已提供:
- `CLAUDE.md` — 项目宪法(硬规则 1-6 + Shanghan ReleaseGate 红线)
- `.mcp.json` — 注册 `shanghan-hermes` MCP server(stdio)
- `.claude/skills/safety-review` — 合并前安全核查 skill
- `.claude/agents/` — schema-guardian / clinical-rule-auditor 子智能体

打开仓库后执行 `/mcp` 应能看到 shanghan-hermes,可直接调用:
`shanghan_ask` / `shanghan_match` / `shanghan_stats` / `nrs_pain`。

LLM 后端: 设置环境变量
```bash
export HERMES_LLM_MODEL="anthropic/claude-sonnet-5"   # 任意 litellm 模型名
export HERMES_LLM_API_BASE="..."                         # 可选,院内私有化端点
```
