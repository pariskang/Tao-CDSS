"""mcp-drug-safety-server — 过敏/相互作用/禁忌/剂量范围。

硬规则1: 本 server 是全系统唯一合法剂量来源;此处数据为工程占位,
生产环境必须对接药学部审定的结构化数据库。
"""
from __future__ import annotations

# PENDING_PHYSICIAN_REVIEW: 占位数据,仅用于管线联调,严禁临床使用
_DOSE_TABLE = {
    ("ibuprofen", "adult"): {"min_mg": 200, "max_mg": 400, "interval_h": 6,
                             "max_daily_mg": 1200},
}

_INTERACTIONS = {
    frozenset({"warfarin", "aspirin"}): "出血风险增加",
}


def check_allergy(drug_id: str, allergies: list[str]) -> dict:
    hit = drug_id in allergies
    return {"drug_id": drug_id, "allergy_conflict": hit,
            "matched": [a for a in allergies if a == drug_id]}


def check_interaction(drug_ids: list[str]) -> dict:
    findings = []
    for pair, risk in _INTERACTIONS.items():
        if pair <= set(drug_ids):
            findings.append({"drugs": sorted(pair), "risk": risk})
    return {"interactions": findings}


def get_dose_range(drug_id: str, population: str = "adult") -> dict:
    """结构化剂量(唯一合法剂量源)。未知药物返回 None 字段,绝不猜测。"""
    entry = _DOSE_TABLE.get((drug_id, population))
    return {
        "drug_id": drug_id,
        "population": population,
        "dose": entry,
        "source": "drug_safety_server",
        "review": "PENDING_PHYSICIAN_REVIEW",
    }


TOOLS = {
    "drug_safety.check_allergy": check_allergy,
    "drug_safety.check_interaction": check_interaction,
    "drug_safety.get_dose_range": get_dose_range,
}
