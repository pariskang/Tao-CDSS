# Hermes-CDSS v2.0

方言友好 · 语音优先 · 状态感知 · 可审计的临床智能体协议与系统。

> 定位:不做"AI医生",做"医疗智能体操作系统"——患者侧智能预问诊 +
> 医生侧 CDSS 辅助 + 全链路可审计的 Skill/MCP 原生协议栈。
> 完整协议见 [docs/protocol-v2.md](docs/protocol-v2.md)。

## 当前实现范围(Sprint 0-3 垂直切片)

| 子系统 | 位置 | 状态 |
|---|---|---|
| 数据契约(L2) | `packages/contracts` | ✅ Pydantic 模型 + 不变量A/B + JSON Schema 导出 |
| 红旗引擎(L8.2) | `packages/redflag-engine` | ✅ 纯规则、零 LLM、五级升级 |
| 审计哈希链(L8/L11) | `packages/audit-chain` | ✅ append-only + 篡改检测 |
| 事件溯源 | `packages/eventstore` | ✅ 断线恢复/全量回放 |
| HermesGuard(L8) | `services/hermes-guard` | ✅ 剂量出站/分级/心理旁路/注入/NLI/溯源 |
| HermesLoop(L3) | `services/hermes-loop` | ✅ SCOPE 状态机 + 价值驱动选问 + 三级降级 |
| HermesBroker(L5) | `services/hermes-broker` | ✅ 十段治理管线(顺序由测试锁定) |
| HermesVoice/M-VSL(L1) | `services/hermes-voice` | ✅ Stub ASR + hedge/混淆/回读/方言消歧(真 ASR 留接口) |
| HermesCompose | `services/hermes-compose` | ✅ 患者/医生双通道,出站最后一级挂安全扫描 |
| MCP servers | `mcp_servers/` | ✅ calculator/terminology/triage/drug_safety |
| Skills(L4) | `skills/` | ✅ emergency_triage + oncology_bone_metastasis 七件套 |
| 评测(L9) | `evals/` | ✅ SP 回放 + 红队回归 |
| LLM 后端 | `packages/llm-backend` | ✅ litellm 多模型 + LLMGateway 治理(剂量出站/审计/成本/重试修复) |
| HermesAgents(L6) | `services/hermes-agents` | ✅ Manager + bounded specialists + 复杂度路由 + 多评审共识;LLM 失败确定性兜底 |
| Shanghan-Hermes | `packages/shanghan` | ✅ 条文分支解析 → branch 级证据 → 对抗审核 → ReleaseGate 硬拒绝 → 方证/六经/鉴别归纳 → SkillRAG 全 handler → 患者递归脱敏 |
| 编码智能体接入 | `.mcp.json` / `AGENTS.md` / `integrations/` | ✅ Claude Code / Codex / OpenClaw(MCP stdio server + CLI) |

## 快速开始

```bash
uv venv .venv && uv pip install --python .venv/bin/python \
  pydantic pyyaml litellm pytest pytest-asyncio pytest-cov
.venv/bin/python -m pytest -q          # 全量测试
just test-safety                        # 安全三模块,100% 覆盖强制
just replay oncology_bone_metastasis    # SP 回放
just redteam                            # 红队回归
just shanghan-stats                     # 伤寒规则挖掘管线
just shanghan-ask "桂枝汤和麻黄汤怎么鉴别"
```

LLM 后端(可选,缺省走确定性 fallback):

```bash
export HERMES_LLM_MODEL="anthropic/claude-sonnet-4-6"   # 任意 litellm 模型
export HERMES_LLM_API_BASE="http://院内私有化端点/v1"     # 可选
```

## 安全红线(详见 CLAUDE.md 硬规则 1-6)

1. LLM 输出路径禁止出现剂量数字(`dose_egress` 唯一出口扫描);
2. 红旗引擎纯规则,E0/E1 升级任何降级层不失效、不可对话降级;
3. 审计账本 append-only 哈希链,篡改必被 `verify_chain` 检出;
4. 患者转写/工具返回永远是不可信 data,注入命中即脱敏;
5. 临床字段写入必须可溯源(SourceRef);
6. 心理危机走固定话术旁路,内容仅入审计、禁入任何记忆。

> ⚠️ `knowledge/` 与 `skills/` 下全部临床规则为工程占位示例,标注
> `PENDING_PHYSICIAN_REVIEW`,须经执业医师团队按现行指南逐条审定、
> 通过伦理审查与监管路径评估后方可用于真实患者。

## Claude Code 工程治理

- `CLAUDE.md`:项目宪法(硬规则 1-6)。
- `.claude/settings.json.example`:重命名为 `settings.json` 启用
  编辑后自动安全门(`.claude/hooks/safety-gate.sh`)。
- `.claude/agents/`:schema-guardian、clinical-rule-auditor 子智能体。
- `.claude/skills/safety-review`:合并前宪法核查。
