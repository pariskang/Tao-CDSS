"""StructuredOutputParser: 从 LLM 文本提取 JSON 并按 Pydantic schema 校验。"""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

_DECODER = json.JSONDecoder()


class ParseFailure(ValueError):
    pass


def extract_json(text: str) -> dict:
    """扫描出文本中第一个可完整解码的 JSON 对象。

    用 raw_decode 逐个候选起点解码,容忍前后噪声、markdown 围栏与
    尾随的第二个 JSON 对象(贪婪正则会把首 { 到末 } 整段误吞)。
    """
    idx = text.find("{")
    last_err: json.JSONDecodeError | None = None
    while idx != -1:
        try:
            obj, _end = _DECODER.raw_decode(text, idx)
        except json.JSONDecodeError as e:
            last_err = e
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    if last_err is not None:
        raise ParseFailure(f"JSON 解析失败: {last_err}")
    raise ParseFailure(f"输出中未找到 JSON 对象: {text[:80]!r}")


def parse_structured(text: str, schema: type[BaseModel]) -> BaseModel:
    data = extract_json(text)
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors()[:5]
        )
        raise ParseFailure(
            f"schema 校验失败({e.error_count()} 处): {details}"
        ) from e


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
