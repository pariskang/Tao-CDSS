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
from shanghan.retrieval import BM25Index

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
        self._clause_index = {c.clause_id: c for c in self._clauses}
        self._formula_names = sorted(result.patterns, key=len, reverse=True)
        self._bm25 = BM25Index({c.clause_id: c.text for c in self._clauses})
        from shanghan.conformal import calibrate_from_patterns

        self._conformal = calibrate_from_patterns(result.patterns)

    # ------------------------------------------------------------------ route
    def route(self, question: str) -> str:
        if any(k in question for k in ("科普", "怎么调理", "我该注意")):
            return "patient"
        if any(k in question for k in ("论文", "paper", "研究报告")):
            return "paper"
        if any(k in question for k in ("误治", "坏病", "反下", "误汗", "误下")):
            return "mistreatment"
        # 鉴别优先于禁忌: "A和B的鉴别与禁忌"这类混合问句按鉴别处理,
        # 鉴别 handler 的输出天然携带两方的判别信息
        if any(k in question for k in ("鉴别", "区别", "怎么分")):
            return "differential"
        if any(k in question for k in ("禁忌", "不可与", "不能用")):
            return "contraindication"
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
        from shanghan.conformal import normalize_scores

        symptoms, pulses = self._parse_findings(question)
        matches = self._matcher.match(symptoms, pulses)
        # 与校准同变换: 每查询 softmax 归一后进入 conformal 判定
        decision = self._conformal.decide(
            normalize_scores({m.formula: m.score for m in matches})
        )
        return {
            "answer": "方证匹配候选(仅供执业医师参考,需四诊合参)",
            # split-conformal 选择性预测: 置信不足即弃权转医师,
            # 覆盖保证基于 engineer_seed 自校准分布(PENDING 医师病例集)
            "conformal": {
                "abstain": decision.abstain,
                "prediction_set": decision.prediction_set,
                "coverage_target": decision.coverage_target,
                "calibration_n": decision.calibration_n,
                "reason": decision.reason,
                "review": "PENDING_PHYSICIAN_REVIEW",
            },
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

    def _rules_of_type(self, rule_type: str, question: str) -> list[dict]:
        """按类型取规则;问句提及具体方剂时只保留与之相关的规则,
        避免把全库禁忌/误治一股脑返回造成误导。"""
        mentioned = {f for f in self._formula_names if f in question}
        views = []
        for ar in self._result.approved:
            if ar.release_level == "rejected" or ar.rule.rule_type != rule_type:
                continue
            if mentioned:
                concl = ar.rule.then_conclusions
                related = {
                    concl.get("formula"), concl.get("forbidden_formula")
                } & mentioned
                span_related = any(
                    f in (ar.rule.evidence_span or "") for f in mentioned
                )
                if not related and not span_related:
                    continue
            views.append(self._rule_view(ar))
        return views

    def _h_mistreatment(self, question: str) -> dict:
        rules = self._rules_of_type("mistreatment_rule", question)
        return {"answer": "误治/变证救治规则", "rules": rules}

    def _h_contraindication(self, question: str) -> dict:
        rules = self._rules_of_type("contraindication_rule", question)
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
            # 无条号时: 实体词锚定 + BM25 混合排序(单字符包含匹配已废弃)
            symptoms, pulses = self._parse_findings(question)
            terms = list(symptoms + pulses)
            terms.extend(f for f in self._formula_names if f in question)
            hits = self._rank_clauses(question, terms)
        return {
            "answer": "条文检索结果" if hits else
                      "未检索到相关条文(问句中未识别出条号或已收录的证候/方剂名)",
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
        answer = "方证鉴别对"
        if len(mentioned) >= 2:
            # 按问句出现顺序取前两个方剂
            ordered = sorted(mentioned, key=question.find)
            want = set(ordered[:2])
            exact = [p for p in pairs if {p.formula_a, p.formula_b} == want]
            if not exact:
                # 找不到精确对时明确说"没有",绝不回退到无关鉴别对误导使用者
                return {
                    "answer": f"当前语料未归纳出 {'、'.join(ordered[:2])} 的"
                              "共有证候鉴别对(可能两方证候无交集或语料未覆盖)",
                    "pairs": [],
                }
            pairs = exact
        return {
            "answer": answer,
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
        hits = self._rank_clauses(question, terms)
        return {
            "answer": "原文证据检索(generic)",
            "evidence": [
                {"clause_id": c.clause_id, "text": c.text} for c in hits[:5]
            ],
        }

    # ------------------------------------------------------------------ helpers
    def _rank_clauses(self, question: str, terms: list[str]) -> list:
        """RRF 混合检索(Reciprocal Rank Fusion, Cormack et al. SIGIR 2009):
        实体词精确命中榜与 BM25(字符bigram)榜按 Σ 1/(60+rank) 融合。
        RRF 免调权、对两路分数尺度不敏感,是零训练混合检索的标准做法。
        确定性: 两榜与融合均以 clause_id 破平。"""
        k_rrf = 60
        fused: dict[str, float] = {}
        # 榜一: 实体词命中数
        if terms:
            anchor_scored = sorted(
                (
                    (sum(1 for t in set(terms) if t in c.text), c.clause_id)
                    for c in self._clauses
                ),
                key=lambda x: (-x[0], x[1]),
            )
            for rank, (hits, cid) in enumerate(anchor_scored, start=1):
                if hits > 0:
                    fused[cid] = fused.get(cid, 0.0) + 1.0 / (k_rrf + rank)
        # 榜二: BM25
        for rank, (cid, score) in enumerate(
            self._bm25.search(question, top_k=len(self._clauses)), start=1
        ):
            if score > 0:
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (k_rrf + rank)
        ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))
        return [self._clause_index[cid] for cid, _ in ranked]

    def _evidence_for(self, clause_ids: list[str]) -> list[dict]:
        return [
            {"clause_id": cid, "text": self._clause_index[cid].text}
            for cid in clause_ids
            if cid in self._clause_index
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
