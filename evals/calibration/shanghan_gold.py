"""金标准校准(评审第九条 / P2-3)。

gold/silver/bronze 是工程置信等级,不是外部验证准确率。
本工具按专家标注(correct/partial/incorrect)计算各等级
precision/recall/F1 与分数分布,用于校准 SCORE_WEIGHTS 与 RELEASE_THRESHOLDS。

当前 knowledge/shanghan/gold_standard.jsonl 为工程种子标注
(engineer_seed,PENDING_PHYSICIAN_REVIEW),正式校准须由中医执业医师
按"正确/部分正确/错误"三级重标后执行。
"""
from __future__ import annotations

import json
from pathlib import Path

from hermes_contracts.paths import repo_root

LABELS = ("correct", "partial", "incorrect")


def rule_key(rule) -> str:
    target = (
        rule.then_conclusions.get("formula")
        or rule.then_conclusions.get("forbidden_formula")
        or rule.then_conclusions.get("forbidden_therapy")
        or rule.then_conclusions.get("channel")
        or ""
    )
    return f"{rule.clause_id}|{rule.rule_type}|{target}"


def load_gold(path: str | Path | None = None) -> dict[str, str]:
    p = Path(path) if path else repo_root() / "knowledge" / "shanghan" / "gold_standard.jsonl"
    gold: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["label"] not in LABELS:
            raise ValueError(f"非法标注: {row['label']}")
        gold[f"{row['clause_id']}|{row['rule_type']}|{row['target']}"] = row["label"]
    return gold


def evaluate(result=None, gold: dict[str, str] | None = None) -> dict:
    if result is None:
        from shanghan.runtime import default_result

        result = default_result()
    gold = gold if gold is not None else load_gold()

    levels = ("gold", "silver", "bronze")
    per_level = {
        lvl: {"total": 0, "correct": 0, "partial": 0, "incorrect": 0,
              "unlabeled": 0, "scores": []}
        for lvl in levels
    }
    released_correct = 0
    rejected_correct = 0
    for ar in result.approved:
        label = gold.get(rule_key(ar.rule))
        if ar.release_level == "rejected":
            if label == "correct":
                rejected_correct += 1
            continue
        bucket = per_level[ar.release_level]
        bucket["total"] += 1
        bucket["scores"].append(ar.score)
        if label is None:
            bucket["unlabeled"] += 1
        else:
            bucket[label] += 1
            if label == "correct":
                released_correct += 1

    gold_correct_total = sum(1 for v in gold.values() if v == "correct")
    report: dict = {"levels": {}, "labels_total": len(gold)}
    for lvl in levels:
        b = per_level[lvl]
        labeled = b["correct"] + b["partial"] + b["incorrect"]
        precision_strict = b["correct"] / labeled if labeled else None
        precision_lenient = (
            (b["correct"] + b["partial"]) / labeled if labeled else None
        )
        report["levels"][lvl] = {
            "total": b["total"],
            "correct": b["correct"],
            "partial": b["partial"],
            "incorrect": b["incorrect"],
            "unlabeled": b["unlabeled"],
            "precision_strict": round(precision_strict, 4) if precision_strict is not None else None,
            "precision_lenient": round(precision_lenient, 4) if precision_lenient is not None else None,
            "score_min": round(min(b["scores"]), 4) if b["scores"] else None,
            "score_max": round(max(b["scores"]), 4) if b["scores"] else None,
        }
    recall = released_correct / gold_correct_total if gold_correct_total else None
    overall_labeled = sum(
        report["levels"][lvl]["correct"] + report["levels"][lvl]["partial"]
        + report["levels"][lvl]["incorrect"] for lvl in levels
    )
    overall_correct = sum(report["levels"][lvl]["correct"] for lvl in levels)
    precision = overall_correct / overall_labeled if overall_labeled else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall else None
    )
    report["recall_strict"] = round(recall, 4) if recall is not None else None
    report["precision_strict_overall"] = (
        round(precision, 4) if precision is not None else None
    )
    report["f1_strict"] = round(f1, 4) if f1 is not None else None
    report["rejected_correct"] = rejected_correct
    report["disclaimer"] = (
        "当前标注为 engineer_seed(PENDING_PHYSICIAN_REVIEW),"
        "本报告不构成外部验证准确率;医师重标后方可用于阈值校准。"
    )
    return report


def main() -> None:  # pragma: no cover
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
