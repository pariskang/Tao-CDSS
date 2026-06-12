# CHANGELOG

## 2026-06-12 (剩余优化项全量落地,PR #3)
- 语法位置识别(P1-4): 前置强度标记(宜/与/可与/属)、治法禁忌
  (不可发汗/不可下)、指代禁忌("不可服之"回指消解,referential 标注,
  证据回源走指代路径)。
- 语料 26→39 条(含 38/42/61/64/82/83/84/102/103/243/309/323/350),
  方剂词典 14→23;管线 39 规则全放行(gold 28 / silver 11)。
- 版本异文/注释对齐(评审十二): 锚点加权 + 一对一最大匹配 +
  whitelist/blacklist + difflib 有序差异;demo 异文集 5 条全部正确对齐。
- 金标准校准工具链(评审九): gold_standard.jsonl(engineer_seed)+
  precision/recall/F1 报告 + `just shanghan-calibration`。
- ManagerAgent 专科注册表路由(SPECIALTY_MAP),替换占位"取第一个"。
- FormulaMatcher 缺失核心惩罚按核心数归一化。
- 测试 499 项全绿(新增 29)。

## 2026-06-12 (深度审核修复轮)
- 修复 brancher 主方判定: 前置处置方剂比主方更长时被误判为主方
  (改为收集全部提及,优先带强度/否定标记者并取最后提及)。
- 修复 EvidenceVerifier 两处回源缺口: absent(否定条件)未绑定
  condition_span;conclusion_offsets 未与原文对齐校验。
- AutoRepair 同步清理不在 span 内的 absent 条件。
- SixChannelInducer 改为注入 corpus index(尊重自定义语料根目录);
  方证 associated 列表去重。
- 新增 shanghan.runtime 共享缓存,agents 与 MCP server 复用同一份管线结果。
- _h_formula 对无 pattern 方剂增加防御回退;每个修复均有回归测试,
  测试总数 470 项全绿。

## 2026-06-12 (Shanghan-Hermes + LLM 多智能体层)
- 新增 packages/llm-backend:litellm 统一后端(OpenAI/Anthropic/Gemini/MiniMax/
  私有化)+ LLMGateway(静态 system 模板/剂量出站/JSON schema 校验/重试修复/
  成本日志/审计哈希链)+ PromptRegistry/AgentRoleConfig。
- 新增 services/hermes-agents(协议 L6):Manager + bounded specialists
  (Intake/SafetyTriage/TCMReasoning/MedicationSafety)、复杂度路由、
  受限会诊(≤3 独立输出,禁群聊)、ReviewPanel 多评审共识;全部 Agent
  带确定性 fallback,LLM 失败不阻断。
- 新增 packages/shanghan(Shanghan-Hermes 子系统),外部评审 P0/P1 全部
  设计内置:ClauseBrancher 分支解析(防条件污染)、branch 级证据
  (condition_span/conclusion_span/offsets)、EvidenceVerifier 区间绑定、
  ReleaseGate 硬拒绝(semantic fail/无方剂方证/空规则)、AutoRepair
  只删不造+删方剂即拒绝+修复后重验、特异性加权方证归纳、
  六经 posthoc_induction 标注、SkillRAG 11-handler 全闭环、
  患者端递归脱敏(含古方剂量)、PaperDraftGenerator(明确草稿定位)。
- 新增 MCP stdio server(零依赖 JSON-RPC)+ .mcp.json + AGENTS.md +
  integrations/{claude-code,codex,openclaw} 接入文档。
- 测试 465 项全绿(新增 118);诚实表述边界与金标准校准协议见
  docs/shanghan-hermes.md。

## 2026-06-11 (Sprint 0-3 垂直切片)
- 初始化 monorepo:contracts/redflag-engine/audit-chain/eventstore 四个共享包。
- 实现安全三件套:剂量出站扫描(含全角/零宽/拆字/中文数字绕行对抗)、
  纯规则红旗引擎(五级升级、否定窗口)、审计哈希链(append-only+篡改检测)。
- 实现 SCOPE 编排:确定性状态机(不变量A/B)、价值驱动选问器、
  三级降级控制器、事件溯源 LoopEngine(断线恢复)。
- 实现 HermesGuard:scope 五级分级、心理危机旁路(固定话术/禁入记忆)、
  注入扫描、Claim-Evidence NLI 检查、溯源检查。
- 实现 HermesBroker 十段治理管线(顺序测试锁定)+ 幂等 + PHI 最小化。
- 实现 M-VSL:hedge/否定检测、近音药名双确认、数字回读、方言消歧、StubASR。
- 实现 MCP servers:calculator(NRS/ECOG/CURB-65 确定性计算)、
  terminology、triage、drug_safety(唯一合法剂量源)。
- 落地 emergency_triage 与 oncology_bone_metastasis 两个 skill 七件套
  (脊髓压迫三联征 must-not-miss 闭环)。
- 评测:SP 回放(红旗召回率 100%)+ 红队回归(注入成功率=0,剂量泄漏=0)。
- 测试 342 项全绿;安全三模块覆盖率 100%(CI 强制)。
- 工程决策:当前阶段 LoopEngine 为同步实现(LLM 仅作可选增强,失败自动降级);
  真 ASR/TTS、FastAPI 服务化、PostgreSQL 迁移按 Sprint 6+ 路线图接入。
