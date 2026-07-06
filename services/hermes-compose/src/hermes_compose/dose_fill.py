"""剂量回填 — 硬规则1的"合法回填"半环(此前只有涂抹,filled_spans 是死代码)。

全系统唯一允许出现剂量数字的生成路径:
  drug_safety.get_dose_range 结构化数据 → 本模块渲染文本并计算 filled_spans
  → scan_outbound 以 filled_spans 放行区间内数字。
LLM 生成的任何剂量数字不经过本路径,仍被出站扫描置换。
本模块禁止接受自由文本剂量输入——只消费 drug_safety 的结构化 dict。
"""
from __future__ import annotations

from hermes_guard.dose_egress import scan_outbound


def render_dose_advisories(
    drug_ids: list[str] | tuple[str, ...], population: str = "adult"
) -> list[dict]:
    """按药渲染剂量建议(医生端专用,患者通道禁止调用)。

    返回项: drug_id / text / filled / dose(原结构化数据) / review。
    无审定数据的药物显式声明"无剂量数据",绝不猜测(fail-safe)。
    """
    from mcp_servers.drug_safety.server import get_dose_range

    advisories: list[dict] = []
    for drug_id in drug_ids:
        info = get_dose_range(drug_id, population)
        dose = info.get("dose")
        if not dose:
            advisories.append(
                {
                    "drug_id": drug_id,
                    "text": f"{drug_id}: 无审定剂量数据,不提供剂量建议,"
                            "请医生自行核对药学数据库。",
                    "filled": False,
                    "dose": None,
                    "review": info.get("review"),
                }
            )
            continue
        text = (
            f"{drug_id}: 单次 {dose['min_mg']}-{dose['max_mg']}mg,"
            f"间隔不少于{dose['interval_h']}小时,"
            f"单日不超过{dose['max_daily_mg']}mg"
            f"(来源: drug_safety 结构化回填,{info.get('review')})"
        )
        # 整句由结构化数据渲染,标记为已回填区间;出站扫描仍执行,
        # 确保放行的只有本句——防御后续拼接
        egress = scan_outbound(text, ((0, len(text)),))
        advisories.append(
            {
                "drug_id": drug_id,
                "text": egress.text,
                "filled": True,
                "dose": dose,
                "review": info.get("review"),
            }
        )
    return advisories
