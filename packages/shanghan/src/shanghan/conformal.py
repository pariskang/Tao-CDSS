"""Split-conformal 方证匹配弃权(selective prediction / 转人工)。

理论: split conformal prediction 给出分布无关的有限样本覆盖保证
(Vovk et al. 2005; Angelopoulos & Bates 2023 教程): 取校准集不合规分数
α_i = -score(真方剂),阈值 q̂ = 第 ⌈(n+1)(1-α)⌉ 小的 α_i,则预测集
{f: -score(f) ≤ q̂} 以 ≥1-α 概率覆盖真方剂(交换性假设下)。
分数经每查询 softmax 归一(normalize_scores)后进入校准与推断——
匹配器原始分数无界,归一使阈值语义可解释且校准/推断同变换。

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


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    """每查询 softmax 归一(固定的、校准无关的变换)。

    split-conformal 的有效性不依赖分数尺度(Angelopoulos & Bates 2023,
    任意固定分数函数均有效),但 FormulaMatcher 原始分数无界——归一后
    不合规分数 1-s ∈ [0,1],阈值与预测集成员判定语义可解释,
    也防止极端大分逃逸阈值语义。校准与推断必须使用同一变换。
    """
    if not scores:
        return {}
    m = max(scores.values())
    exps = {f: math.exp(s - m) for f, s in scores.items()}
    z = sum(exps.values())
    return {f: e / z for f, e in exps.items()}


def _true_normalized_scores(patterns: dict) -> list[tuple[str, float]]:
    """留一式自校准数据: 每个 pattern 的证候喂回匹配器,
    记录(方剂, 真方剂归一化得分)。"""
    from shanghan.matcher import FormulaMatcher

    matcher = FormulaMatcher(patterns)
    out: list[tuple[str, float]] = []
    for formula, p in sorted(patterns.items()):
        symptoms = list(p.core_symptoms) + list(p.associated_symptoms)
        if not symptoms:
            continue
        matches = matcher.match(symptoms, list(p.pulses), top_k=len(patterns))
        norm = normalize_scores({m.formula: m.score for m in matches})
        if formula in norm:
            out.append((formula, norm[formula]))
    return out


def calibrate_from_patterns(patterns: dict, alpha: float = 0.1):
    """自校准(engineer_seed): 校准点为归一化真方剂得分。

    合成校准分布,PENDING_PHYSICIAN_REVIEW: 医师标注真实病例
    (症状集→金标准方剂)后应替换本函数的数据源。
    """
    return ConformalCalibrator(alpha=alpha).fit(
        [s for _, s in _true_normalized_scores(patterns)]
    )


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 区间(二项比例的小样本置信区间,闭式无特殊函数)。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def loo_coverage(patterns: dict, alpha: float = 0.1) -> dict:
    """留一覆盖自检: 每个校准点被留出,用其余 n-1 个点定阈值,
    检验被留出的真方剂是否被覆盖(-s_true ≤ q̂)。

    规则引擎不对校准点拟合,故 LOO 是诚实的覆盖估计;
    输出经验覆盖率 + Wilson 95% CI,供与目标 1-α 对照。
    """
    points = _true_normalized_scores(patterns)
    n = len(points)
    if n < 2:
        return {"n": n, "coverage": None, "ci95": None,
                "note": "校准点不足,无法自检"}
    covered = 0
    evaluable = 0
    for i in range(n):
        rest = [s for j, (_, s) in enumerate(points) if j != i]
        cal = ConformalCalibrator(alpha=alpha).fit(rest)
        q = cal.threshold()
        if q is None:
            continue  # n-1 过小: 该折无有限阈值,跳过并如实计数
        evaluable += 1
        if -points[i][1] <= q:
            covered += 1
    if evaluable == 0:
        return {"n": n, "coverage": None, "ci95": None,
                "note": "全部折无有限阈值(样本过小)"}
    cov = covered / evaluable
    lo, hi = _wilson_ci(covered, evaluable)
    return {
        "n": n,
        "evaluable_folds": evaluable,
        "coverage": round(cov, 4),
        "coverage_target": 1 - alpha,
        "ci95": [round(lo, 4), round(hi, 4)],
        "method": "leave-one-out + Wilson score interval",
        "review": "PENDING_PHYSICIAN_REVIEW(engineer_seed 自校准分布)",
    }
