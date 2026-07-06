# Hermes-CDSS 研究基线与创新路线图

> 本文档由 2026-07 的六路文献深度调研(对话诊断 AI / 序贯诊断 / LLM 不确定性 /
> conformal 预测 / 注入防御 / 中医信息学)综合而成,记录每项工程决策的文献依据、
> 已落地实现与待医师/数据解锁的后续项。所有临床数值(LR、阈值、场景标注)均为
> engineer_seed,PENDING_PHYSICIAN_REVIEW。

## 一、已落地(文献 → 代码映射)

### 1. 序贯问诊 = 贝叶斯实验设计(联合类别互信息 + 成本感知 + 停问准则)
- **实现**: `hermes_loop/question_planner.py` —— lr_table 选问分数 =
  联合类别变量 D 上的真互信息 `EIG(s)=H_cat(q)−E_answer[H_cat(q|a)]`
  (两点模型 `P(yes|d)=L/(1+L)` 有解析闭式);主键 EIG/cost,
  二级决胜为答案平衡度 |P(yes)−P(no)|;`EIG_EPS` 边际价值下限与
  `CONFIDENCE_TAU` 置信停问;红旗/must-not-miss 分支永远优先且不受
  预算与停问影响。
- **依据**:
  - AMIE, *Towards conversational diagnostic AI*, **Nature 642:442-450 (2025)**
    ——每轮"缺失信息+置信度"驱动追问;
  - MAI-DxO / SDBench, arXiv:2506.22405 (Microsoft 2025)——虚拟专家面板中
    Dr. Stewardship 的成本管控与 Dr. Hypothesis 的假设维护;
  - BED-LLM, arXiv:2508.21184——证明必须用联合目标真互信息,逐条目独立
    熵代理会系统性高估"弱触多病"问题(本仓库第一版实现即犯此错,已修正);
  - UoT, NeurIPS 2024, arXiv:2402.03271——平衡切分决胜;
  - MediQ, NeurIPS 2024, arXiv:2406.00922 与 Calibrate-Then-Act,
    arXiv:2602.16699——弃答/停问门;ACTMED, arXiv:2510.18988——EIG/成本选检。

### 2. 方证匹配的选择性预测(split-conformal 弃权)
- **实现**: `shanghan/conformal.py` —— 每查询 softmax 归一(匹配器原始
  分数无界,归一使阈值语义可解释且校准/推断同变换)→ split-conformal
  预测集;校准集不足(n < (1−α)/α)/预测集为空/预测集过大 → 一律弃权
  转执业医师;`loo_coverage()` 留一覆盖自检 + Wilson 95% CI 进入
  `just shanghan-calibration` 报告。
- **依据**: Angelopoulos & Bates, arXiv:2107.07511(分布无关有限样本覆盖;
  任意固定分数函数均有效);小样本覆盖为 Beta 随机变量与 SSBC 修正,
  arXiv:2509.15349;conformal 分诊三动作(release/flag/defer)与
  预测集≈鉴别诊断的人机协同证据, arXiv:2605.20956、arXiv:2401.13744。
  规则引擎不对校准点拟合,故留一覆盖自检是诚实估计。

### 3. LLM 输出的字段级一致性门控(结构化离散语义熵)
- **实现**: `hermes_llm/consistency_gate.py`(纯函数) +
  `LLMGateway.structured_call_consistent` —— k 采样按 Pydantic 字段
  三档计票(safety 全票 / standard 法定多数 / info 仅记录),放行返回
  medoid 真实样本(绝不逐字段拼装,防跨字段不变量"缝合怪");
  **门控只有否决权**,PASS 不豁免 dose_egress/redflag/contracts 下游校验。
- **依据**: Farquhar et al., *Detecting hallucinations using semantic
  entropy*, **Nature 630:625-630 (2024)**;discrete semantic entropy 在
  放射 VLM 的黑盒落地, arXiv:2510.09256;RadFlag 蕴含计数+Conformal Risk
  Control, arXiv:2411.00299;字段级采样稳定性(CMR-EXTR), arXiv:2605.08045;
  "一致≠正确,只作否决", arXiv:2603.21172;口头置信度系统性失准
  (Xiong et al., ICLR 2024)——因此本仓库不采用 verbalized confidence。

### 4. 注入防御纵深(spotlighting 定界 + CJK datamarking + 边界不变量)
- **实现**: `hermes_llm/gateway.py` —— ①不可信数据以
  `«HERMES_DATA_START/END»` 哨兵定界,数据内哨兵字符先替换为全角变体
  (无法伪造边界);②数据字符串叶逐字符插入 U+2063(中文无空格,
  这是 datamarking 的 CJK 等价形式;先剥离预置标记防伪装);③最终
  prompt 断言恰有一对哨兵,违反即拒绝外呼并审计 CRIT;④入站
  injection_scanner 命中即脱敏并审计;⑤system 守则声明边界与标记语义。
- **依据**: Spotlighting, Hines et al. (Microsoft), arXiv:2403.14720
  (datamarking 档在 ASR 与任务保持上最佳);Instruction Hierarchy
  (OpenAI), arXiv:2404.13208;Prompt Infection(多智能体注入传播,
  标记+标签可阻断), arXiv:2410.07283;Microsoft MSRC 三层纵深(2025)与
  Google Gemini 防御经验, arXiv:2505.14534——检测层会被自适应攻击绕过,
  须与隔离层(spotlighting)和确定性出口扫描(dose_egress)叠加。

### 5. 确定性 self-play 仿真评测(AMIE 评测范式移植)
- **实现**: `evals/simulation/selfplay.py` + `scenarios/*.yaml` +
  `just selfplay <skill>` —— 场景省略最终诊断只给 facts 查表;患者
  agent 为确定性查表器(缺失答"记不清了");auto-rater 全免 LLM:
  planted 红旗召回 100% / must-not-miss 处置正确 / 剂量泄漏 0 /
  重复提问预算 / 会话可终止 / 审计链完整。为第 1-4 项提供回归护栏。
- **依据**: AMIE self-play 内外环与 auto-rater 二值指标(Nature
  642:442-450);多模态 AMIE 三步仿真流水线(arXiv:2505.04653,后发表于
  Nature Medicine);g-AMIE 医生监督与 guardrail agent(arXiv:2507.15743)。

### 6. 检索与证据(RRF 混合 + BM25 字符 bigram)
- **实现**: `shanghan/retrieval.py`(Okapi BM25,中文字符 bigram 免分词)
  + `skill_rag._rank_clauses` 的 RRF 融合(实体词榜 × BM25 榜,
  score=Σ1/(60+rank))。
- **依据**: 生物医学 RAG 综述——混合稀疏检索稳定优于单一检索、BM25 为
  最广采用基线, arXiv:2505.01146;RRF, Cormack et al., SIGIR 2009;
  中文 bigram 索引经典结论, Nie & Ren, IP&M 1999。

### 7. 评测统计的科学性
- **实现**: 金标准校准报告附 precision 的 95% 百分位 bootstrap CI
  (固定种子确定性,n<5 如实返回 None)+ conformal 留一覆盖自检
  (Wilson 区间);metrics.yaml 未执行指标显式列 `unenforced`。

## 二、经调研否决/降级的方案(附原因)
- **verbalized confidence**(让模型自报置信): 系统性过度自信
  (Xiong, ICLR 2024;Mind the Gap, arXiv:2506.10769)→ 不采用,
  以采样一致性 + conformal 替代。
- **base64 编码不可信数据**(spotlighting 最强档): 仅高能力模型可解且
  与逐字回显类角色冲突, 增益边际 → 否决,取 datamarking 档。
- **Mondrian/分组 conformal**: 需 398 条扩展语料的分组校准数据,
  小格子会退化 → gated,数据就绪后启用。
- **整对象等价类投票**(本仓库一致性门控第一版): 次要字段措辞差异会
  误杀整体 → 已升级为字段级三档计票。
- **逐条目二值 MI 求和**(本仓库 EIG 第一版): BED-LLM 证明的重复计分
  缺陷 → 已升级为联合类别互信息。

## 三、待解锁项(依赖临床数据/人力,非代码)
1. **LR 数值与 lr_table 启用**: 似然比是临床数据,须医师按文献审定
   (机制已就绪,slots.yaml 注释示例)。
2. **conformal 真实校准集**: 现为方证 pattern 留一自校准;医师标注
   `症状集→金标准方剂` 病例集后替换 `calibrate_from_patterns` 数据源,
   届时覆盖保证才是临床意义上的。
3. **SSBC 小样本修正**: n=39 下 PAC 化会趋于恒弃权,语料扩到 398 条后
   评估启用(arXiv:2509.15349)。
4. **g-AMIE 式 DOCTOR_REVIEW 前患者摘要确认**: 值得吸收的交互设计,
   涉及 SCOPE 相机与话术,建议与医师共同设计后实施。
5. **SEP 式廉价语义熵探针 / Kernel Language Entropy**: 需要模型隐层
   访问或更多采样预算,私有化部署后评估(arXiv:2406.15927;NeurIPS 2024)。
6. **症状共现 PPMI 图谱与方剂相似度网络**(SMGCN, arXiv:2002.08575 一脉):
   可解释增强,安全增益低于已实现项,列为语料扩展后的迭代项。
