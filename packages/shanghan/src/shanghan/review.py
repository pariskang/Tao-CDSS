"""对抗审核管线: SemanticReviewer / ShanghanCritic / AutoRepair / ReleaseGate。

P0 修复(对应外部评审):
  1. ReleaseGate 将 semantic fail 作为硬拒绝条件;
  2. formula_pattern_rule 无方剂结论 → rejected;
  3. AutoRepair 修复后重新跑 schema 校验与证据回源;
  4. AutoRepair 只删不造;删除核心结论(方剂)→ must_reject。

gold/silver/bronze 为工程置信等级,非外部验证准确率;
权重与阈值待 100 条专家金标准集校准(见 docs/shanghan-hermes.md 校准协议)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from shanghan.corpus import load_entity_dict
from shanghan.evidence import EvidenceVerifier
from shanghan.rules import InitialRule

RELEASE_THRESHOLDS = {"gold": 0.90, "silver": 0.78, "bronze": 0.62}

#: 共识分数权重(人为经验权重,待金标准校准)
SCORE_WEIGHTS = {
    "evidence": 0.50,
    "semantic_pass": 0.12,
    "critic_pass": 0.16,
    "structure": 0.08,
    "condition_richness": 0.06,
    "model_confidence": 0.04,
}


@dataclass
class ReviewVerdict:
    verdict: str  # pass | warn | fail
    problems: list[str] = field(default_factory=list)


@dataclass
class ApprovedRule:
    rule: InitialRule
    release_level: str  # gold | silver | bronze | rejected
    score: float
    evidence_verified: bool
    semantic: str
    critic: str
    repairs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class SemanticReviewer:
    """语义审核(确定性);LLM 复审可经 extra_reviewers 注入(多评审共识)。"""

    def __init__(self, posthoc_terms: list[str] | None = None):
        if posthoc_terms is None:
            posthoc_terms = load_entity_dict().get("posthoc_terms", [])
        self._posthoc = list(posthoc_terms)

    def review(self, rule: InitialRule) -> ReviewVerdict:
        problems: list[str] = []
        if rule.rule_type == "formula_pattern_rule" and not rule.then_conclusions.get("formula"):
            problems.append("formula_rule_without_formula")
        if rule.rule_type == "contraindication_rule" and not rule.then_conclusions.get(
            "forbidden_formula"
        ):
            problems.append("contraindication_without_formula")
        if rule.rule_type == "channel_outline_rule" and not rule.then_conclusions.get("channel"):
            problems.append("outline_without_channel")
        for term in rule.condition_terms():
            if term in self._posthoc:
                problems.append(f"posthoc_term_in_conditions:{term}")
        if not rule.if_conditions and not rule.then_conclusions:
            problems.append("empty_rule")
        if problems:
            return ReviewVerdict("fail", problems)
        if not rule.condition_terms() and rule.rule_type == "formula_pattern_rule":
            return ReviewVerdict("warn", ["formula_rule_without_explicit_conditions"])
        return ReviewVerdict("pass")


class ShanghanCritic:
    """伤寒批评者(确定性硬检查): 强度夸大、方-经错配、空条件。"""

    def __init__(self, formula_channels: dict[str, str]):
        self._channels = formula_channels

    def review(self, rule: InitialRule, clause_channel: str) -> ReviewVerdict:
        problems: list[str] = []
        warns: list[str] = []
        strength = rule.then_conclusions.get("strength")
        if strength and strength not in rule.conclusion_span:
            problems.append(f"strength_exaggerated:{strength}")
        formula = rule.then_conclusions.get("formula") or rule.then_conclusions.get(
            "forbidden_formula"
        )
        if formula:
            anchor = self._channels.get(formula)
            if anchor and anchor != clause_channel:
                warns.append(f"channel_mismatch:{formula}:{anchor}!={clause_channel}")
        if (
            rule.rule_type == "formula_pattern_rule"
            and not rule.condition_terms()
            and not rule.if_conditions.get("prior_treatment")
        ):
            problems.append("formula_rule_empty_conditions")
        if problems:
            return ReviewVerdict("fail", problems + warns)
        if warns:
            return ReviewVerdict("warn", warns)
        return ReviewVerdict("pass")


class AutoRepairAgent:
    """只删除或降级,绝不伪造证据。

    - 删除不在 condition_span 内的条件(非核心,可保留规则);
    - 若需删除 then_conclusions.formula(不在 conclusion_span)→ must_reject;
    - 修复后 if_conditions 为空且规则类型要求条件 → must_reject;
    - 修复后必须重跑 schema 校验(P0-3)。
    """

    def repair(self, rule: InitialRule) -> tuple[InitialRule | None, list[str], bool]:
        repairs: list[str] = []
        data = rule.model_dump()
        conds = dict(data["if_conditions"])
        for key in ("symptoms", "pulses", "optional_symptoms", "absent"):
            kept = [t for t in conds.get(key, []) if t in rule.condition_span]
            removed = [t for t in conds.get(key, []) if t not in rule.condition_span]
            for t in removed:
                repairs.append(f"removed_{key}:{t}")
            if kept:
                conds[key] = kept
            elif key in conds:
                del conds[key]
        data["if_conditions"] = conds

        must_reject = False
        formula = data["then_conclusions"].get("formula")
        if formula and formula not in rule.conclusion_span:
            # 删除方剂结论会让方证规则失去核心 → 直接拒绝,不"删到能通过"
            repairs.append(f"would_remove_formula:{formula}")
            must_reject = True

        if (
            data["rule_type"] in ("formula_pattern_rule", "mistreatment_rule")
            and not (
                conds.get("symptoms") or conds.get("pulses")
                or conds.get("prior_treatment")
            )
        ):
            must_reject = True
            repairs.append("empty_conditions_after_repair")

        try:
            repaired = InitialRule.model_validate(data)  # P0-3: 重跑 schema 校验
        except ValidationError as e:
            return None, repairs + [f"schema_invalid_after_repair:{e.error_count()}"], True
        return repaired, repairs, must_reject


class ReviewPipeline:
    def __init__(
        self,
        verifier: EvidenceVerifier,
        semantic: SemanticReviewer,
        critic: ShanghanCritic,
        clause_channels: dict[str, str],
        extra_reviewers: list | None = None,
    ):
        self._verifier = verifier
        self._semantic = semantic
        self._critic = critic
        self._channels = clause_channels
        self._extra = extra_reviewers or []  # LLM 多评审(可选,签名: rule -> ReviewVerdict)
        self._repairer = AutoRepairAgent()

    def review(self, rule: InitialRule) -> ApprovedRule:
        repairs: list[str] = []
        problems: list[str] = []

        report = self._verifier.verify(rule)
        if not report.ok:
            repaired, repairs, must_reject = self._repairer.repair(rule)
            if repaired is None or must_reject:
                return ApprovedRule(
                    rule=rule, release_level="rejected", score=0.0,
                    evidence_verified=False, semantic="fail", critic="fail",
                    repairs=repairs, problems=report.problems,
                )
            rule = repaired
            report = self._verifier.verify(rule)  # 修复后重新回源
            problems.extend(report.problems)

        sem = self._semantic.review(rule)
        clause_channel = self._channels.get(rule.clause_id, "")
        crit = self._critic.review(rule, clause_channel)
        extra_verdicts = [r(rule) for r in self._extra]
        # 多评审共识: 任一 fail 即 fail(安全优先)
        sem_result = sem.verdict
        crit_result = crit.verdict
        if any(v.verdict == "fail" for v in extra_verdicts):
            crit_result = "fail"
            problems.extend(p for v in extra_verdicts for p in v.problems)

        score = self._consensus_score(rule, report.ok, sem_result, crit_result)
        level = self._release_gate(score, report.ok, sem_result, crit_result, rule)
        return ApprovedRule(
            rule=rule,
            release_level=level,
            score=round(score, 4),
            evidence_verified=report.ok,
            semantic=sem_result,
            critic=crit_result,
            repairs=repairs,
            problems=problems + sem.problems + crit.problems,
        )

    @staticmethod
    def _consensus_score(rule: InitialRule, evidence_ok: bool, sem: str, crit: str) -> float:
        score = 0.0
        if evidence_ok:
            score += SCORE_WEIGHTS["evidence"]
        if sem == "pass":
            score += SCORE_WEIGHTS["semantic_pass"]
        elif sem == "warn":
            score += SCORE_WEIGHTS["semantic_pass"] / 2
        if crit == "pass":
            score += SCORE_WEIGHTS["critic_pass"]
        elif crit == "warn":
            score += SCORE_WEIGHTS["critic_pass"] / 2
        if (
            rule.then_conclusions.get("strength") == "主之"
            or rule.rule_type in ("channel_outline_rule", "contraindication_rule")
        ):
            score += SCORE_WEIGHTS["structure"]
        richness = min(len(rule.condition_terms()), 3) / 3
        score += richness * SCORE_WEIGHTS["condition_richness"]
        score += rule.model_confidence * SCORE_WEIGHTS["model_confidence"]
        return score

    @staticmethod
    def _release_gate(
        score: float, evidence_ok: bool, sem: str, crit: str, rule: InitialRule
    ) -> str:
        """P0-1/P0-2: semantic fail 与无方剂的方证规则均为硬拒绝条件。"""
        if not evidence_ok:
            return "rejected"
        if sem == "fail":
            return "rejected"
        if crit == "fail":
            return "rejected"
        if rule.rule_type == "formula_pattern_rule" and not rule.then_conclusions.get(
            "formula"
        ):
            return "rejected"
        if not rule.if_conditions and not rule.then_conclusions:
            return "rejected"
        if score >= RELEASE_THRESHOLDS["gold"]:
            return "gold"
        if score >= RELEASE_THRESHOLDS["silver"]:
            return "silver"
        if score >= RELEASE_THRESHOLDS["bronze"]:
            return "bronze"
        return "rejected"
