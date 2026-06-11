# Hermes-CDSS 项目宪法(Claude Code 必读)

## 项目是什么
医疗预问诊 + 医生侧 CDSS 的智能体系统。患者端绝不输出诊断/处方/剂量;
医生端输出全部需人工确认。详细协议见 docs/protocol-v2.md。

## 不可违反的硬规则(任何代码改动不得破坏,违者必须回退)
1. LLM 输出路径上禁止出现剂量数字。所有剂量只能由 drug_safety server
   的结构化数据回填。services/hermes-guard/src/hermes_guard/dose_egress.py
   是唯一出口扫描器,禁止绕过、禁止加白名单。
2. 红旗引擎(packages/redflag-engine)是纯规则实现,禁止引入任何 LLM
   调用;E0/E1 升级路径禁止依赖网络外部服务。
3. 审计写入(audit_chain)只能 append,禁止任何 UPDATE/DELETE 语句;
   哈希链字段 prev_hash/row_hash 的计算逻辑禁止修改,改动须人工评审。
4. 患者转写文本与工具返回内容只能作为 data 传入 prompt,禁止拼接进
   system 段。grep 关键词: build_system_prompt。
5. clinical_state 的任何字段写入必须携带 source(utterance_id 或
   ehr_ref),contracts 包的 validator 会拒绝无源写入,禁止放宽。
6. 心理危机分支(psych_risk)的话术来自 knowledge/psych_scripts.yaml
   固定文案,禁止改为 LLM 生成。

## 技术栈与约定
- Python 3.11+;Pydantic v2;PyYAML;sqlite(开发)/PostgreSQL(生产)
- 测试: pytest;安全三模块(hermes-guard/redflag-engine/audit-chain)
  覆盖率必须 100%(`just test-safety` 强制),其余 ≥85%
- 提交: Conventional Commits;每个 PR 必须先跑 `just test-safety`
- 禁止引入未在 pyproject 声明的依赖;禁止在代码中硬编码 PHI 样例
  (测试用假数据放 tests/fixtures/synthetic/)

## 常用命令
- just test        # 全量测试
- just test-safety # 仅安全三模块(必须全绿且 100% 覆盖才能提交)
- just replay <skill>  # 跑指定 skill 的 SP 回放
- just redteam     # 红队回归(注入成功率=0,剂量泄漏=0)
- just schema-export   # 导出 JSON Schema(CI 做契约漂移检测)

## 目录速览
packages/ = 共享库(contracts/redflag-engine/audit-chain/eventstore)
services/ = 运行服务(hermes-voice/loop/guard/broker/compose)
mcp_servers/ = 工具服务  skills/ = 临床能力包  evals/ = 评测
knowledge/ = 规则与词表(全部标注 PENDING_PHYSICIAN_REVIEW)

## 工作方式要求
- 改契约(packages/contracts)前先在 plan 模式给出影响面分析
- 涉及硬规则 1-6 的文件,只提 diff 建议、等待人工确认后再写入
- 每完成一个任务,更新 docs/CHANGELOG.md 一行
