"""StructuredOutputParser: 从 LLM 文本提取 JSON 并按 Pydantic schema 校验。"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class ParseFailure(ValueError):
    pass


def extract_json(text: str) -> dict:
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ParseFailure(f"输出中未找到 JSON 对象: {text[:80]!r}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ParseFailure(f"JSON 解析失败: {e}") from e


def parse_structured(text: str, schema: type[BaseModel]) -> BaseModel:
    data = extract_json(text)
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise ParseFailure(f"schema 校验失败: {e.error_count()} 处错误") from e


# ---------------------------------------------------------------- 常用输出模型
class ReviewVerdictModel(BaseModel):
    verdict: str
    problems: list[str] = []


class IntakeExtraction(BaseModel):
    symptoms: list[str] = []
    onset: str | None = None
    medications: list[str] = []


class SummaryModel(BaseModel):
    bullets: list[str] = []


SCHEMAS: dict[str, type[BaseModel]] = {
    "ReviewVerdictModel": ReviewVerdictModel,
    "IntakeExtraction": IntakeExtraction,
    "SummaryModel": SummaryModel,
}
