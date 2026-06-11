"""置信度与确认策略(协议 L1.2 asr_confidence_policy)。"""
from __future__ import annotations

CRITICAL_TERMS = (
    "drug_name",
    "allergy",
    "pregnancy",
    "chest_pain",
    "dyspnea",
    "bleeding",
    "loss_of_consciousness",
    "suicidal_expression",
)


def decide(confidence: float, critical: bool = False) -> str:
    """返回: accept | accept_uncertain | reask | confirm。

    关键词置信度 <0.90 必须复述确认;
    0.65–0.85 接受但标记 uncertain,禁用于排除危重鉴别。
    """
    if critical and confidence < 0.90:
        return "confirm"
    if confidence >= 0.85:
        return "accept"
    if confidence >= 0.65:
        return "accept_uncertain"
    return "reask"
