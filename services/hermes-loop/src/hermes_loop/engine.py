"""HermesLoop 编排引擎(SCOPE 协议,协议 L3)。

设计要点:
  - 所有状态变更经事件(_emit → eventstore + reducer)落库,支持断线恢复与回放(坑1);
  - 每步先过红旗引擎(纯规则,任何相、任何降级层都执行);
  - 心理危机旁路优先于一切:固定话术、内容仅入审计、禁入事件流原文(硬规则6);
  - LLM 仅作可选增强,失败自动降级,选问与红旗不依赖 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from audit_chain import AuditLedger
from eventstore import EventStore
from hermes_contracts import (
    MAX_QUESTIONS,
    ClinicalState,
    ConsentFlags,
    Differential,
    DifferentialItem,
    EscalationLevel,
    Phase,
    SourceRef,
    Symptom,
    SymptomStatus,
)
from hermes_contracts.paths import repo_root
from hermes_guard import psych_monitor
from hermes_loop.degradation import DegradationController
from hermes_loop.question_planner import Slot, next_question
from hermes_loop.skill_loader import Skill
from hermes_loop.state_machine import set_escalation, transition
from hermes_voice.mvsl import confidence as confidence_policy
from hermes_voice.mvsl.hedge import detect as hedge_detect
from redflag_engine import RedFlagEngine

DEFAULT_SLOTS: list[Slot] = [
    Slot(name="onset", question="这种不舒服是什么时候开始的?", priority=10, required=True),
    Slot(name="severity", question="不舒服的程度怎么样,0到10分您打几分?", priority=20, required=True),
    Slot(name="associated_symptoms", question="还有没有其他不舒服,比如发烧、出汗、恶心?", priority=30),
    Slot(name="medication_history", question="最近在吃什么药吗?", priority=40),
    Slot(name="allergy", question="有没有药物或食物过敏?", priority=41, required=True),
]


@dataclass
class StepResult:
    actions: list[dict] = field(default_factory=list)
    state: ClinicalState | None = None


class LoopEngine:
    def __init__(
        self,
        encounter_id: str,
        skill: Skill | None = None,
        *,
        store: EventStore | None = None,
        ledger: AuditLedger | None = None,
        llm=None,
        degradation: DegradationController | None = None,
        root: Path | None = None,
    ):
        self.encounter_id = encounter_id
        self.skill = skill
        self._store = store or EventStore()
        self.ledger = ledger or AuditLedger()
        self._llm = llm
        self.degradation = degradation or DegradationController(
            ledger=self.ledger, encounter_id=encounter_id
        )
        self._root = root or repo_root()

        rf_paths = [self._root / "knowledge" / "red_flags" / "global.yaml"]
        self.redflag = RedFlagEngine.from_yaml(*rf_paths)
        if skill and skill.red_flag_rules:
            self.redflag = RedFlagEngine(self.redflag.rules + skill.red_flag_rules)

        self.slots: list[Slot] = list(skill.slots) if skill else list(DEFAULT_SLOTS)
        self._escalation_scripts = yaml.safe_load(
            (self._root / "knowledge" / "escalation_scripts.yaml").read_text(
                encoding="utf-8"
            )
        )
        lex = yaml.safe_load(
            (self._root / "knowledge" / "dialect_symptom_lexicon.yaml").read_text(
                encoding="utf-8"
            )
        )
        self._lexicon: dict[str, list[str]] = lex.get("lexicon", {})

        self.state = ClinicalState(encounter_id=encounter_id)
        if skill and skill.must_not_miss:
            self.state.differential = Differential(
                must_not_miss=[
                    DifferentialItem(
                        condition=m.condition,
                        probability=m.probability,
                        status="not_excluded",
                        discriminators_pending=list(m.discriminators),
                    )
                    for m in skill.must_not_miss
                ]
            )
        self.texts: list[str] = []
        self._utt = 0
        self._awaiting_slot: str | None = None
        self._clarified: set[str] = set()
        self._psych_kind = "self_harm"

    # ------------------------------------------------------------------ events
    def _emit(self, event_type: str, payload: dict) -> None:
        self._store.append(self.encounter_id, event_type, payload)
        self._apply_event({"event_type": event_type, "payload": payload})

    def _apply_event(self, event: dict) -> None:
        etype, p = event["event_type"], event["payload"]
        if etype == "encounter_started":
            self.state.reporter = p.get("reporter", "self")
        elif etype == "phase_change":
            transition(self.state, Phase(p["to"]), actor=p.get("actor", "system"))
        elif etype == "utterance":
            self.texts.append(p["text"])
            self._utt += 1
        elif etype == "psych_trigger":
            self._utt += 1
            self._psych_kind = p.get("kind", "self_harm")
        elif etype == "escalation":
            set_escalation(
                self.state, EscalationLevel(p["level"]), actor=p.get("actor", "system")
            )
            self.state.red_flag_hits = sorted(
                set(self.state.red_flag_hits) | set(p.get("hits", []))
            )
        elif etype == "red_flag_hits":
            self.state.red_flag_hits = sorted(
                set(self.state.red_flag_hits) | set(p.get("hits", []))
            )
        elif etype == "chief_complaint":
            self.state.chief_complaints.append(p["text"])
        elif etype == "symptom_added":
            self.state.symptoms.append(Symptom(**p["symptom"]))
        elif etype == "slot_answered":
            self.state.answered_slots[p["slot"]] = p["status"]
            if self._awaiting_slot == p["slot"]:
                self._awaiting_slot = None
        elif etype == "must_not_miss_update":
            for item in self.state.differential.must_not_miss:
                if item.condition == p["condition"]:
                    item.status = p["status"]
                    item.discriminators_pending = list(p["discriminators_pending"])
        elif etype == "question_asked":
            self.state.asked_slots.append(p["slot"])
            self.state.question_count = self.state.question_count + 1
            self._awaiting_slot = p["slot"]
        elif etype == "clarify_requested":
            self._clarified.add(p["term"])
        elif etype == "degraded":
            self.state.degraded_mode = p["level"]
            self.degradation.level = p["level"]
        # doctor_action / summary 等事件无状态副作用

    @classmethod
    def resume(
        cls,
        encounter_id: str,
        skill: Skill | None = None,
        *,
        store: EventStore,
        ledger: AuditLedger | None = None,
        root: Path | None = None,
    ) -> "LoopEngine":
        """断线恢复:由事件流重建到同一相(协议 L3 SESSION_RESUME)。"""
        eng = cls(encounter_id, skill, store=store, ledger=ledger, root=root)
        for ev in store.events(encounter_id):
            eng._apply_event(ev)
        return eng

    # ------------------------------------------------------------------ flow
    def begin(
        self, consent: ConsentFlags | None = None, reporter: str = "self"
    ) -> ClinicalState:
        if self.state.phase != Phase.CONSENT:
            raise RuntimeError("会话已开始,禁止重复 begin(请用 resume 恢复)")
        consent = consent or ConsentFlags()
        self._emit(
            "encounter_started",
            {"consent": consent.model_dump(), "reporter": reporter},
        )
        self.ledger.append(
            self.encounter_id,
            "system",
            "encounter_started",
            {"consent": consent.model_dump(), "reporter": reporter},
        )
        self._emit("phase_change", {"to": Phase.IDENTITY_PROXY.value})
        self._emit("phase_change", {"to": Phase.CHIEF_COMPLAINT.value})
        return self.state

    def step(self, text: str, asr_confidence: float = 0.95) -> StepResult:
        if self.state.phase == Phase.END:
            raise RuntimeError("会话已结束")
        if self.state.phase == Phase.ESCALATION:
            raise RuntimeError("会话已升级,等待人工接管")
        if self.state.phase == Phase.PSYCH_RISK:
            # 通道保持直至人工接管,不返回自动流程
            script = psych_monitor.get_script(self._psych_kind)
            return StepResult(
                actions=[
                    {"type": "psych_hold", "script_id": script["script_id"],
                     "text": script["text"]},
                ],
                state=self.state,
            )

        actions: list[dict] = []
        utt_id = f"utt_{self._utt:03d}"

        # 1) 心理危机旁路优先(硬规则6: 原文仅入审计,禁入事件流/记忆)
        if psych_monitor.triggered(text):
            kind = psych_monitor.detect_kind(text)
            self._psych_kind = kind
            self.ledger.append(
                self.encounter_id,
                "system",
                "psych_trigger",
                {"utterance_id": utt_id, "text": text, "kind": kind},
            )
            self._emit("psych_trigger", {"utterance_id": utt_id, "kind": kind})
            self._emit(
                "phase_change", {"to": Phase.PSYCH_RISK.value, "actor": "system"}
            )
            script = psych_monitor.get_script(kind)
            actions.append(
                {"type": "psych_script", "script_id": script["script_id"],
                 "text": script["text"]}
            )
            actions.append({"type": "notify_human", "reason": "psych_risk"})
            return StepResult(actions=actions, state=self.state)

        # 2) 记录转写事件
        self._emit(
            "utterance",
            {"utterance_id": utt_id, "text": text, "asr_confidence": asr_confidence},
        )

        # 3) LLM 可选增强(失败自动降级,绝不阻断规则路径)
        self._try_llm(text)

        # 4) 红旗扫描(纯规则,永不降级;先于置信度门控——召回优先)
        scan = self.redflag.scan_texts(self.texts)
        if scan.hits:
            self._emit("red_flag_hits", {"hits": scan.hit_ids()})
        if scan.level in ("E0", "E1"):
            return self._escalate(scan, actions)
        if scan.level in ("E2", "E3", "E4"):
            self._maybe_soft_escalation(scan)

        # 5) 低置信转写请求复述(协议 L1.2: <0.65 复述;红旗扫描已在其前)
        if confidence_policy.decide(asr_confidence) == "reask":
            actions.append(
                {"type": "reask", "prompt": "抱歉,我没有听清楚,请您再说一遍。"}
            )
            return StepResult(actions=actions, state=self.state)

        # 6) 已提问槽位的回答归一
        if self._awaiting_slot is not None:
            self._record_answer(self._awaiting_slot, text, utt_id, asr_confidence)

        # 7) 方言俗称消歧 + 槽位关键词抽取
        self._scan_lexicon(text, utt_id, asr_confidence, actions)
        self._match_slot_keywords(text, utt_id, asr_confidence)

        # 8) 相变与选问
        self._advance(text, actions)
        return StepResult(actions=actions, state=self.state)

    # ------------------------------------------------------------------ helpers
    def _try_llm(self, text: str) -> None:
        # 硬规则7: 传入的 llm 可调用对象必须由 hermes_llm.LLMGateway 封装
        # (网关内置剂量出站扫描与审计),禁止直接传裸 SDK/litellm 调用。
        if self._llm is None or self.degradation.level != "L0":
            return
        before = self.degradation.level
        try:
            self._llm(text)
        except Exception:
            self.degradation.record_llm_failure()
        else:
            self.degradation.record_llm_success()
        if self.degradation.level != before:
            self._emit("degraded", {"level": self.degradation.level})

    def _escalate(self, scan, actions: list[dict]) -> StepResult:
        level = scan.level
        self._emit("escalation", {"level": level, "hits": scan.hit_ids()})
        self._emit(
            "phase_change", {"to": Phase.ESCALATION.value, "actor": "system"}
        )
        self.ledger.append(
            self.encounter_id,
            "system",
            "escalation",
            {"level": level, "hits": scan.hit_ids()},
        )
        script = self._escalation_scripts[level]
        actions.append(
            {
                "type": "escalation",
                "level": level,
                "script_id": script["script_id"],
                "text": script["text"],
                "hits": scan.hit_ids(),
            }
        )
        return StepResult(actions=actions, state=self.state)

    def _maybe_soft_escalation(self, scan) -> None:
        from hermes_contracts import ESCALATION_SEVERITY

        cur = self.state.escalation
        if cur is None or (
            ESCALATION_SEVERITY[scan.level] < ESCALATION_SEVERITY[cur.value]
        ):
            self._emit("escalation", {"level": scan.level, "hits": scan.hit_ids()})

    def _record_answer(
        self, slot_name: str, text: str, utt_id: str, conf: float
    ) -> None:
        hedge = hedge_detect(text)
        status = hedge.status
        self._emit("slot_answered", {"slot": slot_name, "status": status.value})
        self._emit(
            "symptom_added",
            {
                "symptom": {
                    "concept_id": slot_name,
                    "raw_text": text,
                    "status": status.value,
                    "severity": hedge.severity,
                    "sources": [
                        {"kind": "utterance", "ref_id": utt_id,
                         "asr_confidence": conf}
                    ],
                    "secondhand": self.state.reporter != "self",
                }
            },
        )
        # must_not_miss 判别闭环: denied 才可移出 pending;
        # probably_denied(hedge否定)置信上限0.7,不可排除(协议 L1/L2)
        for item in self.state.differential.must_not_miss:
            if slot_name in item.discriminators_pending and status == SymptomStatus.DENIED:
                pending = [d for d in item.discriminators_pending if d != slot_name]
                new_status = "excluded" if not pending else item.status
                self._emit(
                    "must_not_miss_update",
                    {
                        "condition": item.condition,
                        "status": new_status,
                        "discriminators_pending": pending,
                    },
                )

    def _scan_lexicon(
        self, text: str, utt_id: str, conf: float, actions: list[dict]
    ) -> None:
        remaining = text
        for term in sorted(self._lexicon, key=len, reverse=True):
            if term not in remaining:
                continue
            candidates = self._lexicon[term]
            remaining = remaining.replace(term, "□" * len(term))
            if len(candidates) > 1:
                if term not in self._clarified:
                    self._emit("clarify_requested", {"term": term})
                    actions.append(
                        {
                            "type": "clarify",
                            "topic": "部位消歧" if ("痛" in term or "疼" in term) else "症状消歧",
                            "term": term,
                            "options": candidates,
                        }
                    )
            else:
                self._emit(
                    "symptom_added",
                    {
                        "symptom": {
                            "concept_id": candidates[0],
                            "raw_text": term,
                            "status": hedge_detect(text, term=term).status.value,
                            "sources": [
                                {"kind": "utterance", "ref_id": utt_id,
                                 "asr_confidence": conf}
                            ],
                            "secondhand": self.state.reporter != "self",
                        }
                    },
                )

    def _match_slot_keywords(self, text: str, utt_id: str, conf: float) -> None:
        for slot in self.slots:
            if slot.name in self.state.answered_slots or not slot.keywords:
                continue
            hit = next((k for k in slot.keywords if k in text), None)
            if hit is None:
                continue
            status = hedge_detect(text, term=hit).status
            self._emit("slot_answered", {"slot": slot.name, "status": status.value})
            self._emit(
                "symptom_added",
                {
                    "symptom": {
                        "concept_id": slot.name,
                        "raw_text": hit,
                        "status": status.value,
                        "sources": [
                            {"kind": "utterance", "ref_id": utt_id,
                             "asr_confidence": conf}
                        ],
                        "secondhand": self.state.reporter != "self",
                    }
                },
            )

    def _advance(self, text: str, actions: list[dict]) -> None:
        if self.state.phase == Phase.CHIEF_COMPLAINT:
            self._emit("chief_complaint", {"text": text})
            self._emit("phase_change", {"to": Phase.RED_FLAG.value})
            self._emit("phase_change", {"to": Phase.HPI.value})

        if self.state.phase != Phase.HPI:
            return
        if any(a["type"] == "clarify" for a in actions):
            return  # 本轮以澄清为问句(每轮一问约束)

        q = next_question(self.state, self.slots)
        if q is not None:
            self._emit("question_asked", {"slot": q.slot, "rationale": q.rationale})
            actions.append(
                {"type": "question", "slot": q.slot, "text": q.text,
                 "rationale": q.rationale}
            )
            return

        # 槽位耗尽 → 相变推进至医生审核
        self._emit("phase_change", {"to": Phase.PMH_MED.value})
        self._emit("phase_change", {"to": Phase.SPECIALTY.value})
        self._emit("phase_change", {"to": Phase.DIFFERENTIAL.value})
        blocking = self.state.differential.blocking_items()
        open_disc = {
            d
            for item in blocking
            for d in item.discriminators_pending
            if d not in self.state.asked_slots and d not in self.state.answered_slots
        }
        if blocking and open_disc and self.state.question_count < MAX_QUESTIONS:
            self._emit("phase_change", {"to": Phase.HPI.value})
            q = next_question(self.state, self.slots)
            if q is not None:
                self._emit(
                    "question_asked", {"slot": q.slot, "rationale": q.rationale}
                )
                actions.append(
                    {"type": "question", "slot": q.slot, "text": q.text,
                     "rationale": q.rationale}
                )
                return
        self._emit("phase_change", {"to": Phase.EVIDENCE.value})
        self._emit("phase_change", {"to": Phase.VERIFY.value})
        self._emit("phase_change", {"to": Phase.DOCTOR_REVIEW.value})
        actions.append(
            {
                "type": "awaiting_doctor",
                "unexcluded": [i.condition for i in blocking],
            }
        )

    # ------------------------------------------------------------------ doctor
    def doctor_review(
        self, decision: str = "adopt", doctor_id: str = "doctor", note: str = ""
    ) -> dict:
        """医生采纳/修改/拒绝,全部留痕(协议 L8)。"""
        if self.state.phase != Phase.DOCTOR_REVIEW:
            raise RuntimeError(f"当前相 {self.state.phase} 不可执行医生审核")
        if decision not in ("adopt", "modify", "reject"):
            raise ValueError(f"非法医生决策: {decision}")
        self.ledger.append(
            self.encounter_id,
            doctor_id,
            "doctor_action",
            {"decision": decision, "note": note},
        )
        self._emit("doctor_action", {"decision": decision, "doctor_id": doctor_id})
        blocking = self.state.differential.blocking_items()
        if blocking and self.state.question_count < MAX_QUESTIONS:
            # 不变量B: must_not_miss 未闭环且未达提问上限 → 患者摘要扣留
            return {
                "withheld": True,
                "reason": "must_not_miss_open",
                "unexcluded": [i.condition for i in blocking],
            }
        from hermes_compose.composer import compose_patient_summary

        self._emit("phase_change", {"to": Phase.SUMMARY.value, "actor": "human"})
        summary = compose_patient_summary(self.state)
        self._emit("phase_change", {"to": Phase.END.value, "actor": "human"})
        return {"withheld": False, "summary": summary}

    def human_release_psych(self, actor_id: str, to_phase: Phase = Phase.END) -> None:
        """心理危机通道只能由人工关闭。"""
        self.ledger.append(
            self.encounter_id, actor_id, "psych_release", {"to": to_phase.value}
        )
        self._emit("phase_change", {"to": to_phase.value, "actor": "human"})
