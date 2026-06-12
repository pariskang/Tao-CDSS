"""SkillRAG — 问题路由 + 全 handler 闭环(P0-4 修复)。

route() 返回的每一个 handler 在 ask() 中都有真实的 app 调用:
  match / formula / mistreatment / contraindication / therapy / paper /
  patient / clause / six_channel / differential / generic。
患者角色输出一律经 patient_safety.governed 递归脱敏。
"""
from __future__ import annotations

import re

from shanghan.corpus import load_corpus, load_entity_dict
from shanghan.entities import EntityExtractor
from shanghan.matcher import FormulaMatcher
from shanghan.paper import PaperDraftGenerator
from shanghan.patient_safety import governed
from shanghan.pipeline import PipelineResult

HANDLERS = (
    "match", "formula", "mistreatment", "contraindication", "therapy",
    "paper", "patient", "clause", "six_channel", "differential", "generic",
)

_CLAUSE_NO = re.compile(r"第?\s*(\d+)\s*条")


class SkillRAG:
    def __init__(self, result: PipelineResult, role: str = "doctor"):
        self._result = result
        self._role = role
        self._extractor = EntityExtractor()
        self._matcher = FormulaMatcher(result.patterns)
        self._paper = PaperDraftGenerator()
        self._clauses = load_corpus()
        self._formula_names = sorted(result.patterns, key=len, reverse=True)

    # ------------------------------------------------------------------ route
    def route(self, question: str) -> str:
        if any(k in question for k in ("科普", "怎么调理", "我该注意")):
            return "patient"
        if any(k in question for k in ("论文", "paper", "研究报告")):
            return "paper"
        if any(k in question for k in ("误治", "坏病", "反下", "误汗", "误下")):
            return "mistreatment"
        if any(k in question for k in ("禁忌", "不可与", "不能用")):
            return "contraindication"
        if any(k in question for k in ("鉴别", "区别", "怎么分")):
            return "differential"
        if any(k in question for k in ("六经", "提纲")):
            return "six_channel"
        if _CLAUSE_NO.search(question) or "条文" in question or "原文" in question:
            return "clause"
        if any(k in question for k in ("治法", "怎么治", "治疗原则")):
            return "therapy"
        if any(k in question for k in ("匹配", "什么方证", "用什么方", "应该用")):
            return "match"
        if any(f in question for f in self._formula_names):
            return "formula"
        return "generic"

    # ------------------------------------------------------------------ ask
    def ask(self, question: str, role: str | None = None) -> dict:
        role = role or self._role
        handler = self.route(question)
        if role == "patient":
            handler = "patient"
        payload = getattr(self, f"_h_{handler}")(question)
        payload["handler"] = handler
        payload["role"] = role
        return governed(payload, role)

    # ------------------------------------------------------------------ handlers
    def _parse_findings(self, question: str) -> tuple[list[str], list[str]]:
        mentions = self._extractor.extract(question)
        symptoms = [m.term for m in mentions if m.kind == "symptom" and not m.negated]
        pulses = [m.term for m in mentions if m.kind == "pulse" and not m.negated]
        return symptoms, pulses

    def _h_match(self, question: str) -> dict:
        symptoms, pulses = self._parse_findings(question)
        matches = self._matcher.match(symptoms, pulses)
        return {
            "answer": "方证匹配候选(仅供执业医师参考,需四诊合参)",
            "parsed": {"symptoms": symptoms, "pulses": pulses},
            "matched_formula_patterns": [
                {
                    "formula": m.formula,
                    "score": m.score,
                    "matched_core": m.matched_core,
                    "matched_associated": m.matched_associated,
                    "matched_pulses": m.matched_pulses,
                    "missing_core": m.missing_core,
                    "evidence": self._evidence_for(m.supporting_clauses),
                }
                for m in matches
            ],
        }

    def _h_formula(self, question: str) -> dict:
        name = next((f for f in self._formula_names if f in question), None)
        if name is None or name not in self._result.patterns:
            return self._h_generic(question)
        p = self._result.patterns[name]
        rules = [
            self._rule_view(ar)
            for ar in self._result.approved
            if ar.release_level != "rejected"
            and (
                ar.rule.then_conclusions.get("formula") == name
                or ar.rule.then_conclusions.get("forbidden_formula") == name
            )
        ]
        return {
            "answer": f"{name} 方证规则与原文证据",
            "formula": name,
            "pattern": {
                "core_symptoms": p.core_symptoms,
                "associated_symptoms": p.associated_symptoms,
                "pulses": p.pulses,
                "contraindications": p.contraindications,
                "source_level": p.source_level,
            },
            "rules": rules,
            "evidence": self._evidence_for(p.supporting_clauses),
        }

    def _h_mistreatment(self, question: str) -> dict:
        rules = [
            self._rule_view(ar)
            for ar in self._result.approved
            if ar.release_level != "rejected"
            and ar.rule.rule_type == "mistreatment_rule"
        ]
        return {"answer": "误治/变证救治规则", "rules": rules}

    def _h_contraindication(self, question: str) -> dict:
        rules = [
            self._rule_view(ar)
            for ar in self._result.approved
            if ar.release_level != "rejected"
            and ar.rule.rule_type == "contraindication_rule"
        ]
        return {"answer": "禁忌规则(不可与)", "rules": rules}

    def _h_therapy(self, question: str) -> dict:
        symptoms, pulses = self._parse_findings(question)
        matches = self._matcher.match(symptoms, pulses, top_k=1)
        channels = {
            ch: {"outline_conditions": s.outline_conditions, "formulas": s.formulas}
            for ch, s in self._result.channels.items()
        }
        return {
            "answer": "治法线索 = 方证候选 + 六经归属(医生端参考)",
            "matched_formula_patterns": [
                {"formula": m.formula, "score": m.score} for m in matches
            ],
            "channels": channels,
        }

    def _h_paper(self, question: str) -> dict:
        return self._paper.generate(self._result.stats)

    def _h_patient(self, question: str) -> dict:
        symptoms, _ = self._parse_findings(question)
        outline = [
            {"clause_id": c.clause_id, "text": c.text}
            for c in self._clauses
            if "之为病" in c.text
        ][:3]
        return {
            "answer": "这些表现建议尽早就医,由执业中医师面诊辨证;以下为《伤寒论》相关科普提纲。",
            "explanation": "中医讲究四诊合参,请勿自行对号入座或自行用药。",
            "recognized_symptoms": symptoms,
            "classical_reference": outline,
        }

    def _h_clause(self, question: str) -> dict:
        m = _CLAUSE_NO.search(question)
        if m:
            no = int(m.group(1))
            hits = [c for c in self._clauses if c.no == no]
        else:
            hits = [c for c in self._clauses if any(ch in question for ch in c.text[:6])]
        return {
            "answer": "条文检索结果",
            "clauses": [
                {"clause_id": c.clause_id, "no": c.no, "channel": c.channel,
                 "text": c.text}
                for c in hits[:5]
            ],
        }

    def _h_six_channel(self, question: str) -> dict:
        return {
            "answer": "六经锚定归纳(posthoc_induction: 后世框架约束下的原文锚定,非自主发现)",
            "channels": {
                ch: {
                    "outline_clause": s.outline_clause,
                    "outline_conditions": s.outline_conditions,
                    "formulas": s.formulas,
                    "subtypes": s.subtypes,
                    "source_level": s.source_level,
                }
                for ch, s in self._result.channels.items()
            },
        }

    def _h_differential(self, question: str) -> dict:
        mentioned = [f for f in self._formula_names if f in question]
        pairs = self._result.differentials
        if len(mentioned) >= 2:
            want = set(mentioned[:2])
            exact = [p for p in pairs if {p.formula_a, p.formula_b} == want]
            pairs = exact or pairs
        return {
            "answer": "方证鉴别对",
            "pairs": [
                {
                    "formula_a": p.formula_a,
                    "formula_b": p.formula_b,
                    "shared": p.shared,
                    "discriminators_a": p.discriminators_a,
                    "discriminators_b": p.discriminators_b,
                }
                for p in pairs[:5]
            ],
        }

    def _h_generic(self, question: str) -> dict:
        symptoms, pulses = self._parse_findings(question)
        terms = symptoms + pulses
        hits = [
            c for c in self._clauses if any(t in c.text for t in terms)
        ] if terms else []
        return {
            "answer": "原文证据检索(generic)",
            "evidence": [
                {"clause_id": c.clause_id, "text": c.text} for c in hits[:5]
            ],
        }

    # ------------------------------------------------------------------ helpers
    def _evidence_for(self, clause_ids: list[str]) -> list[dict]:
        index = {c.clause_id: c for c in self._clauses}
        return [
            {"clause_id": cid, "text": index[cid].text}
            for cid in clause_ids
            if cid in index
        ]

    @staticmethod
    def _rule_view(ar) -> dict:
        return {
            "rule_id": ar.rule.rule_id,
            "rule_type": ar.rule.rule_type,
            "release_level": ar.release_level,
            "score": ar.score,
            "if_conditions": ar.rule.if_conditions,
            "then_conclusions": ar.rule.then_conclusions,
            "evidence_span": ar.rule.evidence_span,
            "condition_span": ar.rule.condition_span,
            "conclusion_span": ar.rule.conclusion_span,
            "clause_id": ar.rule.clause_id,
            "branch_id": ar.rule.branch_id,
        }
