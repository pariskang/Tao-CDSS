"""Claim-Evidence Binding 检查器(协议 L8.4)。

contradicted → 拦截+审计;neutral → 折叠区标注"模型推测,无指南依据";
无证据主张不得以平铺方式呈现。
default_nli 为确定性启发式实现(二期换专用 NLI 小模型,接口不变)。
"""
from __future__ import annotations

VERDICTS = ("entailed", "neutral", "contradicted")

_NEG_EVIDENCE = ("不推荐", "禁用", "禁忌", "不应", "避免使用", "不宜")
_POS_CLAIM = ("推荐", "建议", "可用", "应当", "首选")


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def default_nli(claim_text: str, evidence_text: str) -> str:
    if not evidence_text:
        return "neutral"
    if any(m in evidence_text for m in _NEG_EVIDENCE) and any(
        m in claim_text for m in _POS_CLAIM
    ):
        return "contradicted"
    claim_grams = _bigrams(claim_text)
    if not claim_grams:
        return "neutral"
    overlap = len(claim_grams & _bigrams(evidence_text)) / len(claim_grams)
    return "entailed" if overlap >= 0.3 else "neutral"


def check_claim(claim, evidence_texts: dict[str, str], nli=default_nli) -> str:
    if not claim.evidence_bindings:
        return "neutral"
    verdicts = [
        nli(claim.text, evidence_texts.get(b.get("evidence_id", ""), ""))
        for b in claim.evidence_bindings
    ]
    if "contradicted" in verdicts:
        return "contradicted"
    if "entailed" in verdicts:
        return "entailed"
    return "neutral"


def triage_claims(
    claims, evidence_texts: dict[str, str], nli=default_nli
) -> dict:
    """返回 {"presented": [...], "folded": [...], "blocked": [...]}。

    presented=entailed 平铺呈现;folded=neutral 折叠区(模型推测);
    blocked=contradicted 拦截。
    """
    out = {"presented": [], "folded": [], "blocked": []}
    for claim in claims:
        verdict = check_claim(claim, evidence_texts, nli)
        claim.verifier_nli = verdict
        if verdict == "contradicted":
            out["blocked"].append(claim)
        elif verdict == "entailed":
            out["presented"].append(claim)
        else:
            out["folded"].append(claim)
    return out
