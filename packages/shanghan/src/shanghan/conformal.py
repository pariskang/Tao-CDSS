"""Split-conformal 方证匹配弃权(selective prediction / 转人工)。

理论: split conformal prediction 给出分布无关的有限样本覆盖保证
(Vovk et al. 2005; Angelopoulos & Bates 2023 教程): 取校准集不合规分数
α_i = -score(真方剂),阈值 q̂ = 第 ⌈(n+1)(1-α)⌉ 小的 α_i,则预测集
{f: -score(f) ≤ q̂} 以 ≥1-α 概率覆盖真方剂(交换性假设下)。

安全语义(fail-safe):
  - 校准集过小(⌈(n+1)(1-α)⌉ > n,即 n < (1-α)/α)时无有限阈值,
    永远弃权转医师——绝不假装有覆盖保证;
  - 预测集为空或大小 >max_set_size 视为"匹配置信不足",弃权;
  - 当前校准数据为 engineer_seed(方证 pattern 留一自校准),
    覆盖保证针对该合成分布;医师标注真实病例集后方可宣称临床覆盖率。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ConformalDecision:
    abstain: bool
    prediction_set: list[str]
    threshold: float | None
    calibration_n: int
    coverage_target: float
    reason: str = ""


@dataclass
class ConformalCalibrator:
    alpha: float = 0.1  # 目标误覆盖率(coverage ≥ 1-α)
    max_set_size: int = 3
    _scores: list[float] = field(default_factory=list)  # 校准: 真方剂得分

    def fit(self, true_scores: list[float]) -> "ConformalCalibrator":
        self._scores = sorted(float(s) for s in true_scores)
        return self

    @property
    def calibration_n(self) -> int:
        return len(self._scores)

    def threshold(self) -> float | None:
        """不合规分数阈值 q̂(不合规 = -score)。None = 校准集不足。"""
        n = len(self._scores)
        rank = math.ceil((n + 1) * (1 - self.alpha))
        if n == 0 or rank > n:
            return None
        # 第 rank 小的不合规分数 = 第 rank 大的 -score
        nonconformity = sorted(-s for s in self._scores)
        return nonconformity[rank - 1]

    def decide(self, candidate_scores: dict[str, float]) -> ConformalDecision:
        q = self.threshold()
        if q is None:
            return ConformalDecision(
                abstain=True, prediction_set=[], threshold=None,
                calibration_n=self.calibration_n,
                coverage_target=1 - self.alpha,
                reason=f"校准集不足(n={self.calibration_n} < "
                       f"{math.ceil((1 - self.alpha) / self.alpha)}),无有限"
                       "样本覆盖保证,转执业医师",
            )
        pred = sorted(
            (f for f, s in candidate_scores.items() if -s <= q),
            key=lambda f: -candidate_scores[f],
        )
        if not pred:
            return ConformalDecision(
                abstain=True, prediction_set=[], threshold=q,
                calibration_n=self.calibration_n,
                coverage_target=1 - self.alpha,
                reason="所有候选低于校准阈值,匹配置信不足,转执业医师",
            )
        if len(pred) > self.max_set_size:
            return ConformalDecision(
                abstain=True, prediction_set=pred[: self.max_set_size],
                threshold=q, calibration_n=self.calibration_n,
                coverage_target=1 - self.alpha,
                reason=f"预测集过大({len(pred)}>{self.max_set_size}),"
                       "证候区分度不足,转执业医师",
            )
        return ConformalDecision(
            abstain=False, prediction_set=pred, threshold=q,
            calibration_n=self.calibration_n,
            coverage_target=1 - self.alpha,
        )


def calibrate_from_patterns(patterns: dict, alpha: float = 0.1):
    """留一自校准(engineer_seed): 每个方证 pattern 的核心+伴随证候
    喂回匹配器,记录真方剂得分作为校准点。

    这是合成校准分布,PENDING_PHYSICIAN_REVIEW: 医师标注真实病例
    (症状集→金标准方剂)后应替换本函数的数据源。
    """
    from shanghan.matcher import FormulaMatcher

    matcher = FormulaMatcher(patterns)
    true_scores: list[float] = []
    for formula, p in sorted(patterns.items()):
        symptoms = list(p.core_symptoms) + list(p.associated_symptoms)
        if not symptoms:
            continue
        for m in matcher.match(symptoms, list(p.pulses), top_k=len(patterns)):
            if m.formula == formula:
                true_scores.append(m.score)
                break
    return ConformalCalibrator(alpha=alpha).fit(true_scores)
