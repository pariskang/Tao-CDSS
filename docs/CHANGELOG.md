# CHANGELOG

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
