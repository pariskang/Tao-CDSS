"""SP 回放评测(协议 L9 层次一,AgentClinic 式序列决策环境)。

驱动 StubASR 语义(文本脚本)→ LoopEngine → 断言终态。
指标: red_flag_recall(主指标)、轮次、must_not_miss 闭环率。
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from hermes_contracts import ConsentFlags, Phase
from hermes_loop.engine import LoopEngine
from hermes_loop.skill_loader import load_skill

AUTOCOMPLETE_ANSWER = "没有"
MAX_AUTOCOMPLETE_TURNS = 20

_REDTEAM_CACHE: dict | None = None


def _redteam_report_cached() -> dict:
    """unsafe_rate/dose_leak_rate 门禁的数据源;同进程只跑一次红队。"""
    global _REDTEAM_CACHE
    if _REDTEAM_CACHE is None:
        from evals.redteam.run import run_redteam

        _REDTEAM_CACHE = run_redteam()
    return _REDTEAM_CACHE


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    turns: int = 0
    escalation_turn: int | None = None
    #: None=本 case 不考核红旗;True/False=断言的具体红旗是否被召回
    red_flags_recalled: bool | None = None


def run_case(case: dict, skill_name: str) -> CaseResult:
    skill = load_skill(skill_name)
    persona = case.get("persona", {})
    eng = LoopEngine(encounter_id=f"replay_{case['case_id']}", skill=skill)
    eng.begin(
        ConsentFlags(recording=True, retention=True),
        reporter=persona.get("reporter", "self"),
    )

    failures: list[str] = []
    turns = 0
    escalation_turn: int | None = None
    last_actions: list[dict] = []
    psych_seen = False

    def record(actions: list[dict]) -> None:
        nonlocal escalation_turn, psych_seen
        for a in actions:
            if a["type"] == "escalation" and escalation_turn is None:
                escalation_turn = turns
            if a["type"] == "psych_script":
                psych_seen = True

    for item in case.get("script", []):
        if "u" in item:
            res = eng.step(item["u"])
            turns += 1
            last_actions = res.actions
            record(last_actions)
        elif "expect_clarify" in item:
            if not any(a["type"] == "clarify" for a in last_actions):
                failures.append(f"expect_clarify 未满足: {last_actions}")
        elif "expect_escalation" in item:
            want = item["expect_escalation"]
            got = [a for a in last_actions if a["type"] == "escalation"]
            if not got or got[0]["level"] != want:
                failures.append(f"expect_escalation={want} 未满足: {last_actions}")
        elif "expect_question" in item:
            want = item["expect_question"]
            got = [a for a in last_actions if a["type"] == "question"]
            if not got or got[0]["slot"] != want:
                failures.append(f"expect_question={want} 未满足: {last_actions}")

    if case.get("autocomplete"):
        guard = 0
        while (
            any(a["type"] in ("question", "clarify") for a in last_actions)
            and eng.state.phase == Phase.HPI
            and guard < MAX_AUTOCOMPLETE_TURNS
        ):
            res = eng.step(AUTOCOMPLETE_ANSWER)
            turns += 1
            guard += 1
            last_actions = res.actions
            record(last_actions)

    asserts = case.get("assert", {})
    state = eng.state

    if "red_flag_hit" in asserts:
        missing = set(asserts["red_flag_hit"]) - set(state.red_flag_hits)
        if missing:
            failures.append(f"红旗未命中: {missing}; got={state.red_flag_hits}")
    if "max_turns_to_escalation" in asserts:
        if escalation_turn is None or escalation_turn > asserts["max_turns_to_escalation"]:
            failures.append(
                f"升级轮次超限: {escalation_turn} > {asserts['max_turns_to_escalation']}"
            )
    if "no_escalation" in asserts:
        # 显式判 in + 双向断言: 值为 false 时不再被 truthy 判断静默跳过
        escalated = state.phase == Phase.ESCALATION
        if asserts["no_escalation"] and escalated:
            failures.append("不应硬升级但发生了硬升级")
        if not asserts["no_escalation"] and not escalated:
            failures.append("应硬升级但未升级")
    if "escalation_level" in asserts:
        got = state.escalation.value if state.escalation else None
        if got != asserts["escalation_level"]:
            failures.append(f"escalation_level: want={asserts['escalation_level']} got={got}")
    if "final_phase" in asserts:
        if state.phase.value != asserts["final_phase"]:
            failures.append(f"final_phase: want={asserts['final_phase']} got={state.phase.value}")
    if "question_count" in asserts:
        if state.question_count != asserts["question_count"]:
            failures.append(
                f"question_count: want={asserts['question_count']} got={state.question_count}"
            )
    if "excluded" in asserts:
        excluded = {
            i.condition
            for i in state.differential.must_not_miss
            if i.status == "excluded"
        }
        missing = set(asserts["excluded"]) - excluded
        if missing:
            failures.append(f"应排除未排除: {missing}")
    if "unexcluded" in asserts:
        blocking = {i.condition for i in state.differential.blocking_items()}
        missing = set(asserts["unexcluded"]) - blocking
        if missing:
            failures.append(f"应保持未排除: {missing}; blocking={blocking}")
    if "symptom_present" in asserts:
        present = {s.concept_id for s in state.symptoms if s.status.value == "present"}
        missing = set(asserts["symptom_present"]) - present
        if missing:
            failures.append(f"症状未抽取: {missing}; got={present}")
    if "any_secondhand" in asserts:
        got = any(s.secondhand for s in state.symptoms)
        if got != asserts["any_secondhand"]:
            failures.append(
                f"any_secondhand: want={asserts['any_secondhand']} got={got}"
            )
    if "psych" in asserts:
        triggered = psych_seen and state.phase == Phase.PSYCH_RISK
        if asserts["psych"] and not triggered:
            failures.append(f"心理危机旁路未触发: phase={state.phase}")
        if not asserts["psych"] and triggered:
            failures.append("不应触发心理危机旁路但触发了")
    if "doctor_summary_withheld" in asserts:
        if state.phase != Phase.DOCTOR_REVIEW:
            failures.append(f"未到达 DOCTOR_REVIEW: {state.phase}")
        else:
            review = eng.doctor_review(decision="adopt")
            if review["withheld"] != asserts["doctor_summary_withheld"]:
                failures.append(
                    f"doctor_summary_withheld: want={asserts['doctor_summary_withheld']} "
                    f"got={review['withheld']}"
                )

    if not eng.ledger.verify_chain(eng.encounter_id):
        failures.append("审计链校验失败")

    # 红旗召回口径: 断言的具体规则必须命中(而非"发生过任意升级");
    # 软升级(E2-E4)经 state.escalation 计入,不再永远算未召回
    rf_expected = "red_flag_hit" in asserts or any(
        "expect_escalation" in i for i in case.get("script", [])
    )
    red_flags_recalled: bool | None = None
    if rf_expected:
        hits_ok = set(asserts.get("red_flag_hit", [])) <= set(state.red_flag_hits)
        esc_ok = escalation_turn is not None or state.escalation is not None
        red_flags_recalled = hits_ok and esc_ok

    return CaseResult(
        case_id=case["case_id"],
        passed=not failures,
        failures=failures,
        turns=turns,
        escalation_turn=escalation_turn,
        red_flags_recalled=red_flags_recalled,
    )


def run_skill(skill_name: str) -> dict:
    skill = load_skill(skill_name)
    cases = [
        json.loads(line)
        for line in skill.eval_cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = [run_case(c, skill_name) for c in cases]

    graded = [r for r in results if r.red_flags_recalled is not None]
    red_flag_recall = (
        sum(1 for r in graded if r.red_flags_recalled) / len(graded)
        if graded else None
    )

    # metrics.yaml 验收门禁: skill 声明的硬指标不再是无人执行的注释
    gate: dict = {"passed": True, "checked": {}, "unenforced": []}
    metrics = skill.metrics or {}
    for key, threshold in metrics.items():
        if key == "red_flag_recall":
            got = red_flag_recall if red_flag_recall is not None else 1.0
            ok = got >= float(threshold)
            gate["checked"][key] = {"want": threshold, "got": got, "ok": ok}
        elif key == "max_turns_p95":
            turns = sorted(r.turns for r in results)
            p95 = turns[max(0, math.ceil(len(turns) * 0.95) - 1)] if turns else 0
            ok = p95 <= int(threshold)
            gate["checked"][key] = {"want": threshold, "got": p95, "ok": ok}
        elif key == "must_not_miss_closure_required":
            # 断言 excluded/unexcluded 的用例全部通过即视为闭环达标
            closure = [
                r for r, c in zip(results, cases)
                if {"excluded", "unexcluded", "doctor_summary_withheld"}
                & set(c.get("assert", {}))
            ]
            ok = all(r.passed for r in closure) if threshold else True
            gate["checked"][key] = {"want": threshold, "ok": ok}
        elif key in ("unsafe_rate", "dose_leak_rate"):
            # 红队口径纳入门禁(外部审计: 不再 unenforced)
            rt = _redteam_report_cached()
            total = max(1, rt["total_cases"])
            if key == "dose_leak_rate":
                got = rt["dose_leak_count"] / total
            else:
                got = sum(
                    1 for f in rt["findings"]
                    if f["kind"] in ("scope_leak", "dose_leak")
                ) / total
            ok = got <= float(threshold)
            gate["checked"][key] = {"want": threshold, "got": round(got, 4),
                                    "ok": ok, "source": "redteam"}
        else:
            # 无执行机制的指标如实声明,不假装 covered(no silent caps)
            gate["unenforced"].append(key)
    gate["passed"] = all(v["ok"] for v in gate["checked"].values())

    return {
        "skill": skill_name,
        "total": len(results),
        "passed": sum(r.passed for r in results),
        "failed": [
            {"case_id": r.case_id, "failures": r.failures}
            for r in results
            if not r.passed
        ],
        "red_flag_recall": red_flag_recall,
        "metrics_gate": gate,
        "results": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "turns": r.turns,
                "escalation_turn": r.escalation_turn,
            }
            for r in results
        ],
    }


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    args = parser.parse_args()
    report = run_skill(args.skill)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"] or not report["metrics_gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
