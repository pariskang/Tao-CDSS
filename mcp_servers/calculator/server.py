"""mcp-calculator-server — 量表评分全部确定性计算,严禁 LLM 心算(协议 L5)。"""
from __future__ import annotations

_ECOG_DESCRIPTIONS = {
    0: "活动能力完全正常",
    1: "能自由走动及从事轻体力活动",
    2: "能自由走动及生活自理,但已丧失工作能力,日间不少于一半时间可起床活动",
    3: "生活仅能部分自理,日间一半以上时间卧床或坐轮椅",
    4: "卧床不起,生活不能自理",
    5: "死亡",
}


def nrs_pain(score: int) -> dict:
    """NRS 疼痛数字评分分级。score: 0-10。"""
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 10:
        raise ValueError(f"NRS 评分必须为 0-10 的整数: {score!r}")
    band = "none" if score == 0 else "mild" if score <= 3 else "moderate" if score <= 6 else "severe"
    return {"score": score, "band": band, "nursing_flag": band == "severe"}


def ecog(grade: int) -> dict:
    """ECOG 体能状态 0-5;grade>=3 提示功能严重受限(CDSS 标记)。"""
    if not isinstance(grade, int) or isinstance(grade, bool) or not 0 <= grade <= 5:
        raise ValueError(f"ECOG 分级必须为 0-5 的整数: {grade!r}")
    return {
        "grade": grade,
        "description": _ECOG_DESCRIPTIONS[grade],
        "severely_limited": grade >= 3,
    }


def curb65(
    confusion: bool,
    urea_gt7: bool,
    rr_ge30: bool,
    sbp_lt90_or_dbp_le60: bool,
    age_ge65: bool,
) -> dict:
    """CURB-65 肺炎严重度。返回 {score, risk_band} 供医生端展示,禁止直接生成处置结论文本。"""
    score = sum(
        [bool(confusion), bool(urea_gt7), bool(rr_ge30),
         bool(sbp_lt90_or_dbp_le60), bool(age_ge65)]
    )
    return {
        "score": score,
        "risk_band": ["low", "low", "intermediate", "high", "high", "high"][score],
    }


TOOLS = {
    "calculator.nrs_pain": nrs_pain,
    "calculator.ecog": ecog,
    "calculator.curb65": curb65,
}
