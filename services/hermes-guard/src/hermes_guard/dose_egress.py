"""剂量出站扫描器 — 硬规则1的唯一出口。

LLM 在任何环节禁止生成剂量数字;所有剂量只能由 drug_safety server 的
结构化数据回填(filled_spans 标注合法区间)。
禁止绕过本模块、禁止添加白名单。挂在 hermes-compose 出站管线最后一级,
患者端与医生端都过。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

REPLACEMENT = "[剂量须医生确认]"

#: 全角→半角映射(对抗全角混淆绕行),逐字符等长替换以保持区间索引
_FW_TABLE = str.maketrans(
    "０１２３４５６７８９ｍｇｌｕｉｖＭＧＬＵＩＶ．",
    "0123456789mgluivMGLUIV.",
)

#: 数字与单位间允许的分隔符(对抗零宽字符/空格混淆绕行)
_SEP = r"[\s​‌‍⁠﻿~\-]*"
_NUM = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半百]+)"
#: 中文复合单位允许字间分隔(对抗拆字绕行,如"毫 克")
_UNITS = (
    r"(?:mg|mcg|ug|μg|iu|ml|g|毫" + _SEP + r"克|毫" + _SEP + r"升|微"
    + _SEP + r"克|克|片|粒|滴|喷|支|袋|单位|国际单位)"
)
_DOSE = re.compile(_NUM + _SEP + _UNITS, re.IGNORECASE)
_FREQ = re.compile(
    r"(?:每日|每天|一日|每晚|每\s*\d+\s*小时)" + _SEP + _NUM + _SEP + r"次"
    r"|q\d+h|qd\b|bid\b|tid\b|qid\b",
    re.IGNORECASE,
)


@dataclass
class DoseViolation:
    span: tuple[int, int]
    text: str
    pattern: str


@dataclass
class EgressResult:
    text: str
    violations: list[DoseViolation] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.violations


def _overlaps(span: tuple[int, int], filled_spans) -> bool:
    for fs in filled_spans:
        if span[0] < fs[1] and fs[0] < span[1]:
            return True
    return False


def find_dose_mentions(text: str) -> list[tuple[tuple[int, int], str]]:
    """返回文本中所有剂量/频次模式的区间(用于检测与红队断言)。"""
    norm = text.translate(_FW_TABLE)
    found: list[tuple[tuple[int, int], str]] = []
    for name, pat in (("dose", _DOSE), ("freq", _FREQ)):
        for m in pat.finditer(norm):
            found.append((m.span(), name))
    found.sort(key=lambda x: x[0])
    return found


def scan_outbound(
    text: str, filled_spans: tuple[tuple[int, int], ...] = ()
) -> EgressResult:
    """filled_spans = drug_safety server 回填内容的字符区间(唯一合法剂量)。

    区间外命中剂量/频次模式 → 替换为 REPLACEMENT 并记录违规。
    """
    violations: list[DoseViolation] = []
    for span, pattern in find_dose_mentions(text):
        if not _overlaps(span, filled_spans):
            violations.append(
                DoseViolation(span=span, text=text[span[0]:span[1]], pattern=pattern)
            )
    redacted = text
    for v in sorted(violations, key=lambda v: v.span[0], reverse=True):
        redacted = redacted[: v.span[0]] + REPLACEMENT + redacted[v.span[1]:]
    return EgressResult(text=redacted, violations=violations)
