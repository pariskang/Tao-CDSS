"""PaperDraftGenerator — 研究草稿/方法学报告生成器。

定位声明(评审第十四条): 这是模板驱动的 Paper Draft Generator,
不是 Autonomous Paper Writer;不做文献检索/引文校验/期刊格式适配。
升级路径(文献检索/引文校验/审稿人质疑 Agent)见 docs/shanghan-hermes.md。
"""
from __future__ import annotations


class PaperDraftGenerator:
    KIND = "paper_draft"  # 明确为草稿生成器

    def generate(self, stats: dict) -> dict:
        by_level = stats.get("by_level", {})
        body = f"""# 《伤寒论》条文证据驱动的方证规则挖掘:方法学报告(草稿)

> 本文为系统自动生成的研究草稿(Paper Draft),数据为管线统计量,
> 须经研究者核对、补充文献综述与外部验证后方可投稿。

## 方法
对 {stats.get('clauses', 0)} 条宋本《伤寒论》条文执行: 分支切分(ClauseBrancher)
→ 实体抽取 → branch 级规则抽取 → 证据回源(condition_span/conclusion_span 绑定)
→ 对抗审核(语义/批评者/可选 LLM 多评审共识)→ 自动修复(只删不造)
→ ReleaseGate(semantic fail 硬拒绝)。

## 结果
- 初始规则: {stats.get('initial_rules', 0)} 条;放行 {stats.get('released', 0)} 条,拒绝 {stats.get('rejected', 0)} 条。
- 置信分层: gold={by_level.get('gold', 0)}, silver={by_level.get('silver', 0)}, bronze={by_level.get('bronze', 0)}。
- 归纳方证: {stats.get('formula_patterns', 0)} 个。

## 局限
1. gold/silver/bronze 为工程置信等级,未经专家金标准集外部校准;
2. 六经亚型为后世框架锚定(posthoc_induction),非系统自主发现;
3. 方证核心证候判别为特异性加权启发式,需医师审定。

## 待完成
建立 100 条专家金标准条文规则集(正确/部分正确/错误三级标注),
计算 precision/recall/F1 并校准 release score。
"""
        return {
            "kind": self.KIND,
            "title": "《伤寒论》条文证据驱动的方证规则挖掘(草稿)",
            "markdown": body,
            "stats": stats,
        }
