# Shanghan-Hermes 子系统 — 设计与诚实声明

> 条文证据驱动的伤寒理论多智能体知识发现子系统(`packages/shanghan`)。
> 本文档同时是对外表述的"诚实边界":哪些是系统能力,哪些是预置框架。

## 架构

```
clauses.jsonl(宋本条文,公有领域)
  → ClauseBrancher        分支切分(若/或/但/反/不可与;",若"子分支)
  → EntityExtractor       词典最长匹配 + 否定/或然感知
  → InitialRuleExtractor  branch 级规则(condition_span/conclusion_span/offsets)
  → EvidenceVerifier      条件∈condition_span,方剂∈conclusion_span(非 full_text)
  → SemanticReviewer + ShanghanCritic + 可选 LLM 多评审(ReviewPanel)
  → AutoRepairAgent       只删不造;删方剂→must_reject;修复后重跑 schema+回源
  → ReleaseGate           semantic fail / 无方剂方证 / 空规则 = 硬拒绝
  → FormulaPatternInducer(特异性加权 core_score)
  → SixChannelInducer(posthoc_induction)
  → SkillRAG(11 个 handler 全闭环)+ 患者端递归脱敏
```

## ReleaseGate 硬拒绝条件(P0,禁止放宽)

```python
if not evidence_ok: rejected
if sem == "fail": rejected
if crit == "fail": rejected
if formula_pattern_rule and not then_conclusions.formula: rejected
if 空 if_conditions 且 空 then_conclusions: rejected
```

由 `tests/shanghan/test_review_gate.py` 锁定,其中包含
"semantic fail 但 evidence_ok=True 必须 rejected" 的全管线复现用例。

## 诚实表述边界(论文/项目书措辞约束)

| 不可表述为 | 应表述为 |
|---|---|
| 系统自主发现六经亚型 | 在后世六经亚型框架约束下,对原文证据自动锚定与结构化归纳(`posthoc_induction`) |
| gold 规则准确率 ≥90% | gold/silver/bronze 为工程置信等级,未经外部金标准校准 |
| 自动撰写论文 | Paper Draft Generator(研究草稿/方法学报告生成) |
| LLM 自主推理多智能体 | 确定性管线为基线,LLM 经治理网关作为可选评审/抽取增强,失败自动回退 |

## 金标准校准协议(待执行)

1. 由中医执业医师标注 100 条金标准条文规则(正确/部分正确/错误);
2. 计算各 release 等级的 precision/recall/F1;
3. 用标注结果重新校准 SCORE_WEIGHTS 与 RELEASE_THRESHOLDS;
4. 校准前,任何对外材料不得将置信等级表述为准确率。

## LLM 接入(litellm)

```bash
export HERMES_LLM_MODEL="anthropic/claude-sonnet-5"  # 或 openai/gemini/minimax/ollama
export HERMES_LLM_API_BASE="..."                        # 可选,院内私有化端点
```

所有 LLM 调用必须经 `hermes_llm.LLMGateway`:
静态 system 模板(硬规则4)→ 剂量出站扫描(硬规则1)→ JSON schema 校验
→ 重试修复 → 成本日志 → 审计哈希链。无 LLM 时全部 Agent 走确定性 fallback。

## 编码智能体接入

- **Claude Code**: 根目录 `.mcp.json`(shanghan-hermes MCP server)+ `CLAUDE.md`。
- **Codex**: 根目录 `AGENTS.md` + `integrations/codex/`。
- **OpenClaw**: `integrations/openclaw/`(MCP 或 CLI 工具注册)。
- 通用 CLI: `python -m shanghan.cli ask|stats|paper`。

## 已落地的扩展(原 P1/P2 残余项)

- **语法位置识别**(P1-4): 后置"X主之" / 前置"宜X/与X/可与X/属X" /
  否定前置"不可与X" / 治法禁忌"不可发汗" / 指代禁忌"不可服之"
  (回指消解,证据回源走指代路径并显式标注 `referential: true`);
- **版本异文/注释对齐**(`shanghan/variants.py`): 条文号/共享方名/首尾短语
  锚点加权 + 一对一贪心最大匹配 + whitelist/blacklist 人工校勘 +
  difflib 有序差异(保留顺序与上下文);
- **金标准校准工具链**(`evals/calibration/shanghan_gold.py` +
  `knowledge/shanghan/gold_standard.jsonl`): 按 correct/partial/incorrect
  三级标注计算各 release 等级 precision(strict/lenient)/recall/F1 与
  分数分布;当前为 engineer_seed 种子标注,医师重标后用于阈值校准;
- **专科注册表路由**(`hermes_agents/manager.py` SPECIALTY_MAP):
  moderate/complex 按 specialty + tcm_requested + medications 信号选派,
  受限会诊上限不变;
- **matcher 归一化**: 缺失核心惩罚按核心证候数归一,
  避免核心丰富方剂被过度压制。

## 后续升级路径

- 语料从 39 条扩至全本 398 条(方后注/加减法等新形态增量迭代);
- PaperWriter → 文献检索/引文校验/审稿人质疑多 Agent;
- 真实多模型评审(不同基座交叉评审)与专家金标准校准实验。

PENDING_PHYSICIAN_REVIEW: 语料子集、词典、方-经锚定与全部规则
须经中医执业医师审定后方可进入任何临床相关验证。
