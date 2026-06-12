# Hermes-CDSS / Shanghan-Hermes — 编码智能体须知(Codex 等)

本文件与 CLAUDE.md 等效;使用 Codex 时以本文件为项目宪法。

## 不可违反的硬规则
1. LLM 输出路径禁止出现剂量数字;唯一出口扫描器:
   `services/hermes-guard/src/hermes_guard/dose_egress.py`,禁止绕过/加白名单。
   所有 LLM 调用必须经 `hermes_llm.LLMGateway`(内置剂量出站扫描与审计)。
2. `packages/redflag-engine` 纯规则实现,禁止引入 LLM/网络依赖。
3. 审计哈希链(`packages/audit-chain`)只能 append;哈希逻辑改动须人工评审。
4. 不可信内容(转写/工具返回)只能进 user 段 data 字段;
   `hermes_llm.prompts.build_system_prompt` 只接受静态 role 名。
5. clinical_state 写入必须带 SourceRef 溯源。
6. 心理危机话术只读 `knowledge/psych_scripts.yaml` 固定文案。
7. Shanghan ReleaseGate(`packages/shanghan/src/shanghan/review.py`)的
   硬拒绝条件(semantic fail / 无方剂方证规则 / 空规则)禁止放宽。

## 命令
- 测试: `.venv/bin/python -m pytest -q`(改安全模块后必须先跑 `tests/safety`)
- 伤寒管线: `python -m shanghan.cli stats|ask|paper`(需 PYTHONPATH,见 justfile)
- MCP server: `bash scripts/run_shanghan_mcp.sh`

## 接入方式
- Claude Code: 仓库根 `.mcp.json` 已注册 shanghan-hermes MCP server。
- Codex: 在 `~/.codex/config.toml` 注册 MCP server(见 integrations/codex/)。
- OpenClaw: 见 integrations/openclaw/。

所有 knowledge/ 与 skills/ 下临床规则标注 PENDING_PHYSICIAN_REVIEW,
未经执业医师审定不得用于真实患者。
