"""输出五级分级检查器(协议 L8.1)。

class 0 转写/确认 · 1 科普 · 2 导航/摘要 · 3 鉴别/检查建议(仅医生端)
· 4 处方/剂量(仅医生端) · 5 急症升级(固定话术,双通道允许)。
机器侧兜底: class 3-4 内容出现在患者端通道即拦截。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hermes_guard.dose_egress import find_dose_mentions

_CLASS5 = [r"拨打\s*120", r"立即.{0,4}急诊", r"马上.{0,4}急诊"]
_CLASS4 = [
    r"建议(您)?(服用|口服|使用|加用)",
    r"开(具)?(处方|药)",
    r"用法用量",
    r"每(日|天|次).{0,6}(服|吃|用)",
    r"剂量(调整|为)",
]
_CLASS3 = [
    r"诊断(为|是|考虑)",
    r"您?(患有|得了|患的是)",
    r"考虑(为|是).{0,12}(可能性|症|病|炎|癌)",
    r"鉴别诊断",
    r"风险分层",
    r"建议(完善|做|行|进行).{0,10}(检查|化验|CT|MRI|核磁|超声|X线|穿刺)",
]
_CLASS1 = [r"科普", r"健康教育", r"注意休息", r"清淡饮食", r"多(喝|饮)水"]


@dataclass
class ScopeDecision:
    allowed: bool
    output_class: int
    reasons: list[str] = field(default_factory=list)


def classify_text(text: str) -> tuple[int, list[str]]:
    for pats, cls in ((_CLASS5, 5), (_CLASS4, 4), (_CLASS3, 3)):
        hits = [p for p in pats if re.search(p, text)]
        if hits:
            return cls, hits
    # 剂量模式本身即 class 4(即便句式未命中)
    if find_dose_mentions(text):
        return 4, ["dose_pattern"]
    hits = [p for p in _CLASS1 if re.search(p, text)]
    if hits:
        return 1, hits
    return 0, []


def check_text(text: str, channel: str) -> ScopeDecision:
    """channel: patient | doctor。患者端 class 3-4 拦截;class 5 仅限固定话术路径。"""
    cls, reasons = classify_text(text)
    if channel == "patient" and 3 <= cls <= 4:
        return ScopeDecision(allowed=False, output_class=cls, reasons=reasons)
    return ScopeDecision(allowed=True, output_class=cls, reasons=reasons)


def check_claim(claim, channel: str) -> ScopeDecision:
    """对 CDSSClaim:声明分级与文本分级取更严者(max)。"""
    cls, reasons = classify_text(claim.text)
    effective = max(cls, claim.output_class)
    if channel == "patient" and effective >= 3:
        return ScopeDecision(allowed=False, output_class=effective, reasons=reasons)
    return ScopeDecision(allowed=True, output_class=effective, reasons=reasons)
