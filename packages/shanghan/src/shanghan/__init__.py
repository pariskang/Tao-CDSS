"""Shanghan-Hermes — 条文证据驱动的伤寒理论多智能体知识发现子系统。

设计基线(对应外部评审 P0/P1 修复,见 docs/shanghan-hermes.md):
  - branch 级证据: 每条规则携带 condition_span/conclusion_span/offsets/branch_id;
  - ReleaseGate 将 semantic fail / 无方剂的方证规则 / 空规则 作为硬拒绝条件;
  - AutoRepair 只删不造,删除核心结论即标记 must_reject,修复后重跑 schema 校验;
  - SkillRAG 全 handler 闭环;患者端递归脱敏;
  - 六经亚型/方证归纳标注 posthoc_induction(后世框架锚定,非自主发现);
  - gold/silver/bronze 为工程置信等级,非外部验证准确率。
"""
from shanghan.corpus import Clause, load_corpus
from shanghan.pipeline import ShanghanPipeline

__all__ = ["Clause", "ShanghanPipeline", "load_corpus"]
