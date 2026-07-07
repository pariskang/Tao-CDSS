"""MCP 工具的 Pydantic 入参模型 + 受治理注册表(供 HermesBroker 装配)。

参数错误在 Broker schema 段变成 422,不进入工具内部炸成 500。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NRSPainInput(_Strict):
    score: int = Field(ge=0, le=10)


class ECOGInput(_Strict):
    grade: int = Field(ge=0, le=5)


class CURB65Input(_Strict):
    confusion: bool = False
    urea_high: bool = False
    resp_rate_high: bool = False
    low_bp: bool = False
    age_ge_65: bool = False


class RedFlagCheckInput(_Strict):
    texts: list[str] = Field(min_length=1)


class NormalizeSymptomInput(_Strict):
    text: str = Field(min_length=1)
    dialect_region: str | None = None


class MapDrugNameInput(_Strict):
    text: str = Field(min_length=1)


class CheckAllergyInput(_Strict):
    drug_id: str = Field(min_length=1)
    allergies: list[str] = Field(default_factory=list)


class CheckInteractionInput(_Strict):
    drug_ids: list[str] = Field(min_length=1)


class GetDoseRangeInput(_Strict):
    drug_id: str = Field(min_length=1)
    population: str = "adult"


def governed_registry() -> dict:
    """全部 MCP 工具的 ToolSpec 注册表(fn + 输入 schema)。"""
    from hermes_broker.pipeline import ToolSpec
    from mcp_servers.calculator.server import curb65, ecog, nrs_pain
    from mcp_servers.drug_safety.server import (
        check_allergy,
        check_interaction,
        get_dose_range,
    )
    from mcp_servers.terminology.server import map_drug_name, normalize_symptom
    from mcp_servers.triage.server import red_flag_check

    return {
        "calculator.nrs_pain": ToolSpec(nrs_pain, NRSPainInput),
        "calculator.ecog": ToolSpec(ecog, ECOGInput),
        "calculator.curb65": ToolSpec(curb65, CURB65Input),
        "triage.red_flag_check": ToolSpec(red_flag_check, RedFlagCheckInput),
        "terminology.normalize_symptom": ToolSpec(
            normalize_symptom, NormalizeSymptomInput
        ),
        "terminology.map_drug_name": ToolSpec(map_drug_name, MapDrugNameInput),
        "drug_safety.check_allergy": ToolSpec(check_allergy, CheckAllergyInput),
        "drug_safety.check_interaction": ToolSpec(
            check_interaction, CheckInteractionInput
        ),
        "drug_safety.get_dose_range": ToolSpec(
            get_dose_range, GetDoseRangeInput
        ),
    }
