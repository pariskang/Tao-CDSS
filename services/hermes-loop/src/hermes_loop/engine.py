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
from hermes_voice.mvsl import numeric_readback
from hermes_voice.mvsl.confusion_guard import ConfusionGuard
from hermes_voice.mvsl.hedge import detect as hedge_detect
from redflag_engine import RedFlagEngine

#: accept_uncertain 阈值(协议 L1.2): 低于该置信度的否认不得排除危重鉴别
EXCLUSION_MIN_CONFIDENCE = 0.85

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
        self._lr_table: dict = dict(skill.lr_table) if skill else {}
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
        self._confusion = ConfusionGuard.from_yaml(
            self._root / "knowledge" / "drug_confusion_pairs.yaml"
        )

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
        self._concept_texts: list[str] = []
        self._utt = 0
        self._awaiting_slot: str | None = None
        self._clarified: set[str] = set()
        self._pending_clarify: tuple[str, tuple[str, ...]] | None = None
        self._confirmed_drugs: set[str] = set()
        self._psych_kind = "self_harm"

    # ------------------------------------------------------------------ events
    def _emit(self, event_type: str, payload: dict) -> None:
        # 先 apply 后落库: apply 抛错(如非法相变)时事件不落库,
        # 杜绝毒事件损坏事件流导致 resume 永久崩溃
        self._apply_event({"event_type": event_type, "payload": payload})
        self._store.append(self.encounter_id, event_type, payload)

    def _apply_event(self, event: dict) -> None:
        etype, p = event["event_type"], event["payload"]
        if etype == "encounter_started":
            self.state.reporter = p.get("reporter", "self")
        elif etype == "phase_change":
            transition(self.state, Phase(p["to"]), actor=p.get("actor", "system"))
        elif etype == "utterance":
            self.texts.append(p["text"])
            self._concept_texts.extend(self._expand_dialect(p["text"]))
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
            self._pending_clarify = (p["term"], tuple(p.get("options", ())))
        elif etype == "clarify_resolved":
            self._pending_clarify = None
        elif etype == "drug_confirm_requested":
            self._confirmed_drugs.add(p["term"])
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
        #    方言候选概念一并回喂: "心口疼"以[上腹痛,胸痛]参与匹配,
        #    否则方言表述的急症永远不升级
        scan = self.redflag.scan_texts(self.texts + self._concept_texts)
        if scan.hits:
            new_hits = set(scan.hit_ids()) - set(self.state.red_flag_hits)
            if new_hits:  # 只在出现新命中时 emit,避免事件流随对话长度膨胀
                self._emit("red_flag_hits", {"hits": sorted(new_hits)})
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

        # 6) 澄清闭环: 患者对上一轮消歧问句的回答映射回被澄清词
        self._resolve_clarify(text, utt_id, asr_confidence)

        # 7) 已提问槽位的回答归一
        if self._awaiting_slot is not None:
            self._record_answer(self._awaiting_slot, text, utt_id, asr_confidence)

        # 8) 方言俗称消歧 + 槽位关键词抽取
        self._scan_lexicon(text, utt_id, asr_confidence, actions)
        self._match_slot_keywords(text, utt_id, asr_confidence)

        # 9) M-VSL 确认策略(协议 L1.2): 近音药名双确认 + 数字回读,
        #    均为附加确认动作,不阻断问诊流程
        self._check_confusion(text, asr_confidence, actions)
        rb = numeric_readback.check(text)
        if rb is not None:
            actions.append(
                {"type": "readback", "kind": rb.kind,
                 "values": list(rb.values), "prompt": rb.prompt}
            )

        # 10) 相变与选问
        self._advance(text, actions)
        return StepResult(actions=actions, state=self.state)

    # ------------------------------------------------------------------ helpers
    def _try_llm(self, text: str) -> None:
        # 硬规则7: 传入的 llm 可调用对象必须由 hermes_llm.LLMGateway 封装
        # (网关内置剂量出站扫描与审计),禁止直接传裸 SDK/litellm 调用。
        # 降级语义: L0 全功能;L1 受限调用(仅抽取,mode 提示传入)——继续
        # 计数使 L2 自动可达,否则 L1 后 LLM 永不再被调用、L2 成为死档;
        # L2 纯规则,LLM 完全旁路。
        if self._llm is None or self.degradation.level == "L2":
            return
        before = self.degradation.level
        try:
            if self.degradation.level == "L0":
                self._llm(text)
            else:
                self._call_llm_restricted(text)
        except Exception:
            self.degradation.record_llm_failure()
        else:
            self.degradation.record_llm_success()
        if self.degradation.level != before:
            self._emit("degraded", {"level": self.degradation.level})

    def _call_llm_restricted(self, text: str) -> None:
        """L1: 仅抽取模式。支持 mode 关键字的网关封装收到提示;
        旧签名封装退回普通调用(行为不变,仅失败计数继续)。
        签名探测而非 try/except TypeError,避免把封装内部的 TypeError
        误判为签名不符导致双重调用。"""
        import inspect

        try:
            params = inspect.signature(self._llm).parameters
            supports_mode = "mode" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        except (TypeError, ValueError):
            supports_mode = False
        if supports_mode:
            self._llm(text, mode="extract_only")
        else:
            self._llm(text)

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
        # 通知闭环: E0/E1 话术承诺"已通知分诊台",系统必须真的发出
        # notify_human 并留痕,不能只说不做(CDS 闭环原则)
        actions.append(
            {"type": "notify_human", "reason": f"red_flag_{level}",
             "target": "triage_desk", "hits": scan.hit_ids()}
        )
        self.ledger.append(
            self.encounter_id,
            "system",
            "notify_human",
            {"reason": f"red_flag_{level}", "target": "triage_desk",
             "hits": scan.hit_ids()},
        )
        return StepResult(actions=actions, state=self.state)

    def _maybe_soft_escalation(self, scan) -> None:
        from hermes_contracts import ESCALATION_SEVERITY

        cur = self.state.escalation
        if cur is None or (
            ESCALATION_SEVERITY[scan.level] < ESCALATION_SEVERITY[cur.value]
        ):
            self._emit("escalation", {"level": scan.level, "hits": scan.hit_ids()})

    def _expand_dialect(self, text: str) -> list[str]:
        """方言候选概念(供红旗扫描回喂)。被否定的词不扩展,
        避免"没有心口疼"触发假升级;歧义词全部候选都保留(召回优先)。"""
        expanded: list[str] = []
        remaining = text
        for term in sorted(self._lexicon, key=len, reverse=True):
            if term not in remaining:
                continue
            remaining = remaining.replace(term, "□" * len(term))
            status = hedge_detect(text, term=term).status
            if status in (SymptomStatus.DENIED, SymptomStatus.PROBABLY_DENIED):
                continue
            expanded.extend(self._lexicon[term])
        return expanded

    def _resolve_clarify(self, text: str, utt_id: str, conf: float) -> None:
        """把患者对消歧问句的回答映射回被澄清词,记录为可溯源症状。
        未匹配到任何候选则关闭本次澄清(对话继续,不粘滞)。"""
        if self._pending_clarify is None:
            return
        term, options = self._pending_clarify
        chosen = next((o for o in options if o in text), None)
        self._emit(
            "clarify_resolved",
            {"term": term, "concept": chosen, "utterance_id": utt_id},
        )
        if chosen is None:
            return
        self._emit(
            "symptom_added",
            {
                "symptom": {
                    "concept_id": chosen,
                    "raw_text": f"{term}(澄清为{chosen})",
                    "status": hedge_detect(text, term=chosen).status.value,
                    "sources": [
                        {"kind": "utterance", "ref_id": utt_id,
                         "asr_confidence": conf}
                    ],
                    "secondhand": self.state.reporter != "self",
                }
            },
        )

    def _check_confusion(
        self, text: str, conf: float, actions: list[dict]
    ) -> None:
        """近音药名双确认(每药一次): "阿糖腺苷"命中混淆对时追加确认问句。"""
        for term in self._confusion.terms:
            if term not in text or term in self._confirmed_drugs:
                continue
            req = self._confusion.check(term, asr_confidence=conf)
            if req is None:
                continue
            self._emit("drug_confirm_requested", {"term": term})
            actions.append(
                {"type": "confirm", "kind": req.kind, "term": req.term,
                 "prompt": req.prompt, "alternatives": list(req.alternatives)}
            )

    def _record_answer(
        self, slot_name: str, text: str, utt_id: str, conf: float
    ) -> None:
        # 槽位关键词命中时按词窗判极性: "会阴麻木得厉害,但大便没有问题"
        # 全文含"没有"会被误判为否认——恰是 must-not-miss 判别项的假阴性
        slot = next((s for s in self.slots if s.name == slot_name), None)
        term_hit = None
        if slot is not None:
            term_hit = next((k for k in slot.keywords if k in text), None)
        hedge = (
            hedge_detect(text, term=term_hit) if term_hit else hedge_detect(text)
        )
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
        # must_not_miss 判别闭环: 高置信 denied 才可移出 pending;
        # probably_denied 与低置信否认(accept_uncertain 带,<0.85)均不可排除
        # 危重鉴别(协议 L1.2/L2)
        if conf < EXCLUSION_MIN_CONFIDENCE:
            return
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
                    self._emit(
                        "clarify_requested",
                        {"term": term, "options": list(candidates)},
                    )
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

        q = next_question(self.state, self.slots, self._lr_table or None)
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
            # 先确认真有可问槽位再回 HPI: 若 discriminator 名不在 slots 中,
            # 回 HPI 后无问可问会卡死(HPI 无通往 EVIDENCE 的合法相变)
            q = next_question(self.state, self.slots, self._lr_table or None)
            if q is not None:
                self._emit("phase_change", {"to": Phase.HPI.value})
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
        self, decision: str = "adopt", doctor_id: str = "doctor", note: str = "",
        resolved_conditions: dict[str, str] | None = None,
    ) -> dict:
        """医生采纳/修改/拒绝,全部留痕(协议 L8)。

        resolved_conditions: 医生对 must_not_miss 条目的裁决
        (condition → excluded|confirmed_pending),带 doctor 留痕。
        没有该出口时,判别项被答 present 且槽位耗尽的会话会在
        DOCTOR_REVIEW 相永久活锁(withheld 无穷循环)。
        """
        if self.state.phase != Phase.DOCTOR_REVIEW:
            raise RuntimeError(f"当前相 {self.state.phase} 不可执行医生审核")
        if decision not in ("adopt", "modify", "reject"):
            raise ValueError(f"非法医生决策: {decision}")
        for cond, st in (resolved_conditions or {}).items():
            if st not in ("excluded", "confirmed_pending"):
                raise ValueError(f"非法 must_not_miss 裁决: {cond}={st}")
        self.ledger.append(
            self.encounter_id,
            doctor_id,
            "doctor_action",
            {"decision": decision, "note": note,
             "resolved_conditions": dict(resolved_conditions or {})},
        )
        self._emit("doctor_action", {"decision": decision, "doctor_id": doctor_id})
        for cond, st in (resolved_conditions or {}).items():
            for item in self.state.differential.must_not_miss:
                if item.condition == cond:
                    self._emit(
                        "must_not_miss_update",
                        {"condition": cond, "status": st,
                         "discriminators_pending": []},
                    )
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
