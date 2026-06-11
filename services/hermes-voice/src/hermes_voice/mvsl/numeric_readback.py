"""数字复述回读(协议 L1.2 numeric_readback):体温/血压/血糖/剂量/频次一律 TTS 复述确认。"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ReadbackRequest:
    kind: str
    values: tuple[str, ...]
    prompt: str


def check(text: str) -> ReadbackRequest | None:
    nums = _NUM.findall(text)
    if not nums:
        return None
    joined = "、".join(nums)
    return ReadbackRequest(
        kind="numeric",
        values=tuple(nums),
        prompt=f"我和您核对一下数字:{joined},对吗?",
    )
