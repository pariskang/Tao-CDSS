# Hermes-CDSS v2.0 协议(实现对照版)

> 本文为协议正文的实现对照摘要;完整设计文档(国际对标、五大创新点、
> 四条科学假设、P1-P5 论文规划、24个月路线图)以课题申报书为准。

## 八个子系统 → 代码位置

| 协议层 | 子系统 | 代码 |
|---|---|---|
| L1 | HermesVoice / M-VSL | `services/hermes-voice`(hedge/混淆双确认/数字回读/方言消歧;ASR/TTS 先桩后真) |
| L2 | HermesState 临床状态 | `packages/contracts`(ClinicalState/Differential/SourceRef 溯源强制) |
| L3 | HermesLoop / SCOPE | `services/hermes-loop`(状态机+相变硬约束+选问器+复杂度路由预留) |
| L4 | HermesSkill 能力包 | `skills/*` 七件套 + `hermes_loop/skill_loader.py` |
| L5 | HermesBroker MCP 治理 | `services/hermes-broker`(十段管线)+ `mcp_servers/*` |
| L6 | HermesAgents | 预留(Manager + bounded specialists,Sprint 4+) |
| L7 | HermesMemory | 预留(四类记忆+写入四要素,Sprint 7) |
| L8 | HermesGuard 验证星座 | `services/hermes-guard`(五检查器) |
| L9 | HermesEval | `evals/`(SP 回放 + 红队;rubric/校准/公平性 Sprint 8) |
| L10 | 降级协议 | `hermes_loop/degradation.py`(L0/L1/L2,红旗永不降级) |
| L11 | 合规 | 三项分离同意(ConsentFlags)+ 审计哈希链 + PHI 最小化 |
| L12 | 持续学习 | 预留(冷启动→SFT→约束RL→经验记忆) |

## 关键不变量(已由测试锁定)

- **不变量A**:E0/E1 触发后不可被对话降级,只有 `actor="human"` 可降级
  (`state_machine.transition/set_escalation` + 测试)。
- **不变量B**:任一 must_not_miss 为 `not_excluded` 且未达提问上限(12)
  时禁止进入 SUMMARY(contracts 双重校验:赋值前 field_validator 拒绝 +
  构造时 model_validator 兜底)。
- **剂量硬规则**:`dose_egress.scan_outbound` 为唯一出口,drug_safety
  回填区间(filled_spans)外的一切剂量/频次模式替换为「[剂量须医生确认]」。
- **心理旁路**:任意相可入、只出不返自动流程;原文仅入审计账本。
- **probably_denied**(hedge 否定)置信上限 0.7,不可用于排除 must-not-miss。
- **Broker 管线顺序**:auth→consent→allowlist→schema→injection_scan→
  rate_limit→idempotency→execute→egress→audit(测试锁定,顺序即安全语义)。

## 输出五级分级(L8.1)

class 0 转写/确认 · 1 科普 · 2 导航/摘要 · 3 鉴别/检查建议(仅医生端)
· 4 处方/剂量(仅医生端,且剂量只能来自 drug_safety server)
· 5 急症升级(固定话术,双通道)。患者端通道 class 3-4 机器侧拦截。

## 五级急症升级(L8.2)

E0 拨打120 · E1 立即急诊(time-critical 归此级) · E2 当日 · E3 48小时 · E4 常规。
红旗库按主诉组织(`knowledge/red_flags/` + skill `red_flags.yaml`),
召回优先,容忍假阳性;E2-E4 为软升级,流程继续并在医生端标注。

## 产品边界

患者端(class 0-2):预问诊、信息采集、导航、科普、转写确认;
禁止诊断结论、禁止处方、禁止治疗决策。
医生端(class 3-4):鉴别提示、风险分层、证据表、SOAP、缺口;
全部需医生采纳/修改/拒绝并留痕(`doctor_review` 审计)。

> 全部临床规则示例须由执业医师团队按现行指南逐条审定
> (移除 PENDING_PHYSICIAN_REVIEW 标记),并经伦理审查与监管路径
> 评估后方可用于真实患者。
