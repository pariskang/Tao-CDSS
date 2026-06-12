"""bounded specialists — 每个专科 Agent 都是"工具",输出回 Manager 合成。"""
from __future__ import annotations

from hermes_agents.base import AgentResult, BaseAgent
from hermes_contracts.paths import repo_root
from hermes_llm.structured import SCHEMAS
from redflag_engine import RedFlagEngine


class IntakeAgent(BaseAgent):
    """预问诊抽取: LLM 结构化抽取,fallback 走伤寒实体词典。"""

    name = "intake"
    role = "intake_extractor"

    def _llm_run(self, task: dict) -> dict:
        model = self._gateway.structured_call(
            self.role, {"transcript": task.get("texts", [])},
            SCHEMAS["IntakeExtraction"],
        )
        return model.model_dump()

    def fallback(self, task: dict) -> dict:
        from shanghan.entities import EntityExtractor

        extractor = EntityExtractor()
        symptoms: list[str] = []
        for text in task.get("texts", []):
            symptoms += [
                m.term for m in extractor.extract(text)
                if m.kind == "symptom" and not m.negated
            ]
        return {"symptoms": sorted(set(symptoms)), "onset": None, "medications": []}


class SafetyTriageAgent(BaseAgent):
    """急症接管 Agent: 纯规则红旗引擎,无 LLM 路径(硬规则2)。"""

    name = "safety_triage"
    role = ""  # 禁止 LLM

    def __init__(self, gateway=None):
        super().__init__(gateway=None)
        self._engine = RedFlagEngine.from_yaml(
            repo_root() / "knowledge" / "red_flags" / "global.yaml"
        )

    def fallback(self, task: dict) -> dict:
        result = self._engine.scan_texts(list(task.get("texts", [])))
        return {"level": result.level, "hits": result.hit_ids()}


class TCMReasoningAgent(BaseAgent):
    """中医辨证 Agent(仅医生端): 调 Shanghan SkillRAG 作为工具。"""

    name = "tcm_reasoning"
    role = "summarizer"

    def __init__(self, gateway=None, rag=None):
        super().__init__(gateway)
        self._rag = rag

    def _rag_match(self, task: dict) -> dict:
        if self._rag is None:
            from shanghan.pipeline import ShanghanPipeline
            from shanghan.skill_rag import SkillRAG

            self._rag = SkillRAG(ShanghanPipeline().run())
        question = "匹配什么方证? " + " ".join(task.get("texts", []))
        return self._rag.ask(question, role="doctor")

    def _llm_run(self, task: dict) -> dict:
        payload = self._rag_match(task)
        summary = self._gateway.structured_call(
            self.role, {"rag": payload}, SCHEMAS["SummaryModel"]
        )
        payload["summary_bullets"] = summary.model_dump()["bullets"]
        return payload

    def fallback(self, task: dict) -> dict:
        return self._rag_match(task)


class MedicationSafetyAgent(BaseAgent):
    """用药安全 Agent: 包装 drug_safety server(确定性,唯一合法剂量源)。"""

    name = "medication_safety"
    role = ""

    def fallback(self, task: dict) -> dict:
        from mcp_servers.drug_safety.server import check_allergy, check_interaction

        drugs = list(task.get("medications", []))
        allergies = list(task.get("allergies", []))
        return {
            "allergy_checks": [check_allergy(d, allergies) for d in drugs],
            "interactions": check_interaction(drugs)["interactions"],
        }
