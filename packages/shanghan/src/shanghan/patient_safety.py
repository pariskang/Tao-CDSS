"""患者端治理(P0-5): 递归脱敏全部嵌套字段。

患者模式禁止: 诊断结论、方剂推荐、组成/剂量/煎服法。
字符串一律过 hermes_guard.dose_egress(硬规则1)与古方剂量文本脱敏。
"""
from __future__ import annotations

import re

from hermes_guard.dose_egress import scan_outbound

#: 患者端禁止下发的字段(任意嵌套层级)
FORBIDDEN_KEYS = {
    "composition",
    "administration",
    "dosage_processing",
    "recommended_formulas",
    "matched_formula_patterns",
    "formula",
    "forbidden_formula",
}

#: 古籍剂量表达: 中文数词或阿拉伯数字 + 古典单位(两/钱/斤/升/合/枚/铢/丸)。
#: 阿拉伯数字+古典单位("桂枝3两")不会被 dose_egress 的现代单位表覆盖,
#: 必须在此拦住。
_CLASSICAL_NUM = r"(?:[一二两三四五六七八九十半百千万零〇]+|\d+(?:\.\d+)?)"
_CLASSICAL_DOSE = re.compile(
    _CLASSICAL_NUM + r"\s*(?:两|钱|斤|升|合|枚|铢|丸|分(?![钟]))"
)
#: 古典煎服频次("日三服/日再服/一日三服")
_CLASSICAL_FREQ = re.compile(
    r"(?:一?日|每日)\s*[一二两三四五六七八九十再\d]+\s*服"
)
_CLASSICAL_MARK = "[古方剂量已隐去]"


def redact_text_for_patient(text: str) -> str:
    text = _CLASSICAL_DOSE.sub(_CLASSICAL_MARK, text)
    text = _CLASSICAL_FREQ.sub(_CLASSICAL_MARK, text)
    return scan_outbound(text).text


def redact_payload(obj):
    """递归脱敏: dict/list/tuple/set/str 全部层级,禁字段整体剔除。"""
    if isinstance(obj, str):
        return redact_text_for_patient(obj)
    if isinstance(obj, list):
        return [redact_payload(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_payload(x) for x in obj)
    if isinstance(obj, (set, frozenset)):
        return {redact_payload(x) for x in obj}
    if isinstance(obj, dict):
        return {
            k: redact_payload(v)
            for k, v in obj.items()
            if k not in FORBIDDEN_KEYS
        }
    return obj


def governed(payload: dict, role: str) -> dict:
    """医生/科研角色原样放行(出站仍过 compose 层扫描);患者角色递归脱敏。"""
    if role == "patient":
        out = redact_payload(payload)
        out["governance"] = {
            "role": "patient",
            "notice": "本内容仅为健康科普,不构成诊断或处方;具体诊疗请咨询执业中医师。",
        }
        return out
    return payload
