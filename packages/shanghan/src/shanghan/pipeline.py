"""端到端管线: 语料 → 分支 → 规则 → 审核 → 归纳,全程审计入链。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from audit_chain import AuditLedger
from shanghan.corpus import corpus_index, load_corpus, load_formula_dict
from shanghan.evidence import EvidenceVerifier
from shanghan.induction import (
    ChannelSummary,
    DifferentialInducer,
    DifferentialPair,
    FormulaPattern,
    FormulaPatternInducer,
    SixChannelInducer,
)
from shanghan.review import ApprovedRule, ReviewPipeline, SemanticReviewer, ShanghanCritic
from shanghan.rules import InitialRule, InitialRuleExtractor


@dataclass
class PipelineResult:
    initial_rules: list[InitialRule]
    approved: list[ApprovedRule]
    patterns: dict[str, FormulaPattern]
    channels: dict[str, ChannelSummary]
    differentials: list[DifferentialPair]
    stats: dict = field(default_factory=dict)


class ShanghanPipeline:
    def __init__(
        self,
        root: Path | None = None,
        ledger: AuditLedger | None = None,
        extra_reviewers: list | None = None,
    ):
        self.ledger = ledger or AuditLedger()
        self._root = root
        self._index = corpus_index(root)
        formula_dict = load_formula_dict()
        self._review = ReviewPipeline(
            verifier=EvidenceVerifier(self._index),
            semantic=SemanticReviewer(),
            critic=ShanghanCritic(
                {f: meta["channel_anchor"] for f, meta in formula_dict["formulas"].items()}
            ),
            clause_channels={cid: c.channel for cid, c in self._index.items()},
            extra_reviewers=extra_reviewers,
        )

    def run(self) -> PipelineResult:
        clauses = load_corpus(self._root)
        extractor = InitialRuleExtractor()
        initial = extractor.extract_corpus(clauses)
        approved = [self._review.review(r) for r in initial]
        for ar in approved:
            self.ledger.append(
                "shanghan_pipeline",
                "review_pipeline",
                "rule_reviewed",
                {
                    "rule_id": ar.rule.rule_id,
                    "rule_type": ar.rule.rule_type,
                    "release_level": ar.release_level,
                    "score": ar.score,
                    "semantic": ar.semantic,
                    "critic": ar.critic,
                    "repairs": ar.repairs,
                },
            )
        patterns = FormulaPatternInducer().induce(approved)
        channels = SixChannelInducer().induce(approved)
        differentials = DifferentialInducer().induce(patterns)
        released = [a for a in approved if a.release_level != "rejected"]
        stats = {
            "clauses": len(clauses),
            "initial_rules": len(initial),
            "released": len(released),
            "rejected": len(approved) - len(released),
            "by_level": {
                lvl: sum(1 for a in approved if a.release_level == lvl)
                for lvl in ("gold", "silver", "bronze", "rejected")
            },
            "by_type": {
                t: sum(1 for a in released if a.rule.rule_type == t)
                for t in sorted({a.rule.rule_type for a in released})
            },
            "formula_patterns": len(patterns),
        }
        return PipelineResult(
            initial_rules=initial,
            approved=approved,
            patterns=patterns,
            channels=channels,
            differentials=differentials,
            stats=stats,
        )
