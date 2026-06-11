"""hedge/否定/不确定检测(协议 L1.2)。

映射:
  "没有X" → denied
  "好像没有X" → probably_denied(置信上限0.7,不可排除 must-not-miss)
  "有点X" → present(mild, uncertain)
  "记不清" → unknown(槽位保持开放)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from hermes_contracts import SymptomStatus

PROBABLY_DENIED_CONFIDENCE_CAP = 0.7

_UNKNOWN = re.compile(r"记不清|不记得|不清楚|说不上来|想不起")
_PROBABLE_NEG = re.compile(r"(好像|应该|可能|似乎)\s*(没有|没|不|无)")
_MILD = re.compile(r"有点|有些|轻微|一点点|稍微")
_NEG_ANY = re.compile(r"没有|没得|否认")
_NEG_LEAD = re.compile(r"^\s*(没有|没|不|无|否)")


@dataclass(frozen=True)
class HedgeResult:
    status: SymptomStatus
    severity: str | None = None
    confidence_cap: float = 1.0
    uncertain: bool = False


def detect(text: str, term: str | None = None) -> HedgeResult:
    if term is not None:
        return _detect_term(text, term)
    if _UNKNOWN.search(text):
        return HedgeResult(SymptomStatus.UNKNOWN, uncertain=True)
    if _PROBABLE_NEG.search(text):
        return HedgeResult(
            SymptomStatus.PROBABLY_DENIED,
            confidence_cap=PROBABLY_DENIED_CONFIDENCE_CAP,
            uncertain=True,
        )
    if _NEG_ANY.search(text) or _NEG_LEAD.search(text):
        return HedgeResult(SymptomStatus.DENIED)
    if _MILD.search(text):
        return HedgeResult(SymptomStatus.PRESENT, severity="mild", uncertain=True)
    return HedgeResult(SymptomStatus.PRESENT)


def _detect_term(text: str, term: str) -> HedgeResult:
    idx = text.find(term)
    if idx == -1:
        return HedgeResult(SymptomStatus.UNKNOWN, uncertain=True)
    window = text[max(0, idx - 8):idx]
    if _UNKNOWN.search(window):
        return HedgeResult(SymptomStatus.UNKNOWN, uncertain=True)
    if _PROBABLE_NEG.search(window):
        return HedgeResult(
            SymptomStatus.PROBABLY_DENIED,
            confidence_cap=PROBABLY_DENIED_CONFIDENCE_CAP,
            uncertain=True,
        )
    if _NEG_ANY.search(window) or window.endswith(("没", "不", "无", "未")):
        return HedgeResult(SymptomStatus.DENIED)
    if _MILD.search(window):
        return HedgeResult(SymptomStatus.PRESENT, severity="mild", uncertain=True)
    return HedgeResult(SymptomStatus.PRESENT)
