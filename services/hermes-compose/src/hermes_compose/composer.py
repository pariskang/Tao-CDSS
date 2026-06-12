"""HermesCompose 输出合成(协议第二部分)。

患者端: 摘要+科普(class≤2);概率与鉴别诊断仅医生端可见,患者端禁止出现。
全部输出经 HermesGuard 后置扫描(scope_checker + dose_egress)方可下发,
dose_egress 挂在出站管线最后一级(硬规则1)。
"""
from __future__ import annotations

from hermes_contracts import ClinicalState, EscalationLevel
from hermes_guard.dose_egress import scan_outbound
from hermes_guard.evidence_checker import triage_claims
from hermes_guard.scope_checker import check_text


def compose_patient_summary(
    state: ClinicalState,
    dose_fill_spans: tuple[tuple[int, int], ...] = (),
) -> dict:
    """患者端摘要:逐句过 scope 检查,最后整体过剂量出站扫描。"""
    sentences: list[str] = []
    if state.chief_complaints:
        sentences.append("您本次反映的主要不舒服是:" + ";".join(state.chief_complaints) + "。")
    present = [s for s in state.symptoms if s.status.value == "present"]
    denied = [s for s in state.symptoms if s.status.value == "denied"]
    if present:
        sentences.append(
            "已记录到的情况包括:" + "、".join(s.concept_id for s in present) + "。"
        )
    if denied:
        sentences.append(
            "您表示没有:" + "、".join(s.concept_id for s in denied) + "。"
        )
    sentences.append("以上信息将提供给接诊医生,具体诊断和治疗请以医生面诊为准。")

    blocked: list[dict] = []
    kept: list[str] = []
    for sent in sentences:
        decision = check_text(sent, channel="patient")
        if decision.allowed:
            kept.append(sent)
        else:
            blocked.append(
                {"text": sent, "output_class": decision.output_class,
                 "reasons": decision.reasons}
            )
    text = "".join(kept)
    egress = scan_outbound(text, dose_fill_spans)
    return {
        "channel": "patient",
        "text": egress.text,
        "blocked_sentences": blocked,
        "dose_violations": [v.text for v in egress.violations],
    }


def compose_doctor_output(
    state: ClinicalState,
    claims=(),
    evidence_texts: dict[str, str] | None = None,
    dose_fill_spans: tuple[tuple[int, int], ...] = (),
) -> dict:
    """医生端: SOAP+鉴别+证据表+缺口+不确定性;全部需医生采纳/修改/拒绝并留痕。"""
    triaged = triage_claims(list(claims), evidence_texts or {})
    unexcluded = [i.condition for i in state.differential.blocking_items()]

    def render_claim(claim) -> dict:
        egress = scan_outbound(claim.text, dose_fill_spans)
        return {
            "claim_id": claim.claim_id,
            "text": egress.text,
            "claim_type": claim.claim_type,
            "output_class": claim.output_class,
            "verifier_nli": claim.verifier_nli,
            "evidence_bindings": claim.evidence_bindings,
            "dose_violations": [v.text for v in egress.violations],
        }

    return {
        "channel": "doctor",
        "soap": {
            "subjective": {
                "chief_complaints": list(state.chief_complaints),
                "symptoms": [
                    {
                        "concept_id": s.concept_id,
                        "status": s.status.value,
                        "raw_text": s.raw_text,
                        "secondhand": s.secondhand,
                        "sources": [src.model_dump() for src in s.sources],
                    }
                    for s in state.symptoms
                ],
            },
            "objective": {"escalation": state.escalation.value if state.escalation else None},
            "assessment": {
                "must_not_miss": [
                    {
                        "condition": i.condition,
                        "status": i.status,
                        "probability": i.probability,
                        "discriminators_pending": i.discriminators_pending,
                        "flag": "未排除" if i.status == "not_excluded" else "",
                    }
                    for i in state.differential.must_not_miss
                ],
                "most_likely": [
                    {"condition": i.condition, "probability": i.probability}
                    for i in state.differential.most_likely
                ],
            },
            "plan": {"note": "待医生确认", "red_flag_hits": list(state.red_flag_hits)},
        },
        "unexcluded": unexcluded,
        "claims": [render_claim(c) for c in triaged["presented"]],
        "speculation": [render_claim(c) for c in triaged["folded"]],  # 折叠区: 模型推测,无指南依据
        "blocked_claims": [c.claim_id for c in triaged["blocked"]],
        "gaps": {
            "asked_unanswered": [
                s for s in state.asked_slots if s not in state.answered_slots
            ],
            "question_count": state.question_count,
        },
        "degraded_mode": state.degraded_mode,
    }
