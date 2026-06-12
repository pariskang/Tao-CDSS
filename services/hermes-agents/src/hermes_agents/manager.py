"""ManagerAgent + 复杂度自适应路由(协议 L3.3,借鉴 MDAgents)。

simple   → IntakeAgent 单兵直通
moderate → Manager + 对应 specialist
complex  → 受限会诊: ≤3 个 specialist 各自独立输出 → Manager 汇总,
           禁止 specialist 间自由群聊(防一致性偏差)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hermes_agents.base import AgentResult, BaseAgent
from hermes_agents.specialists import (
    IntakeAgent,
    MedicationSafetyAgent,
    SafetyTriageAgent,
    TCMReasoningAgent,
)

MAX_CONSULT_SPECIALISTS = 3

#: 专科 → specialist 名称(协议 L3.3;PENDING: 按院内科室表扩充)
SPECIALTY_MAP: dict[str, tuple[str, ...]] = {
    "tcm": ("tcm_reasoning",),
    "oncology": ("medication_safety", "tcm_reasoning"),
    "respiratory": ("medication_safety",),
    "geriatrics": ("medication_safety",),
}
DEFAULT_SPECIALISTS: tuple[str, ...] = ("tcm_reasoning", "medication_safety")


def classify_complexity(task: dict) -> str:
    complaints = task.get("chief_complaints", [])
    comorbidities = task.get("comorbidities", [])
    if task.get("red_flag_level") in ("E0", "E1"):
        return "complex"
    if len(complaints) > 1 or len(comorbidities) > 1 or task.get("tcm_requested"):
        return "complex"
    if task.get("specialty") or comorbidities:
        return "moderate"
    return "simple"


@dataclass
class ConsultResult:
    complexity: str
    specialist_outputs: list[AgentResult] = field(default_factory=list)
    merged: dict = field(default_factory=dict)


class ManagerAgent(BaseAgent):
    name = "manager"
    role = ""

    def __init__(self, gateway=None, specialists: dict[str, BaseAgent] | None = None):
        super().__init__(gateway)
        self._intake = IntakeAgent(gateway)
        self._safety = SafetyTriageAgent()
        self._registry: dict[str, BaseAgent] = specialists or {
            "tcm_reasoning": TCMReasoningAgent(gateway),
            "medication_safety": MedicationSafetyAgent(),
        }

    def _select_specialists(self, task: dict, complexity: str) -> list[BaseAgent]:
        """专科注册表路由: specialty → SPECIALTY_MAP;tcm_requested/用药信息
        作为补充信号;受限会诊上限 MAX_CONSULT_SPECIALISTS。"""
        names: list[str] = []
        specialty = task.get("specialty")
        if specialty:
            names.extend(SPECIALTY_MAP.get(specialty, DEFAULT_SPECIALISTS[:1]))
        if task.get("tcm_requested"):
            names.append("tcm_reasoning")
        if task.get("medications"):
            names.append("medication_safety")
        if not names:
            names.extend(DEFAULT_SPECIALISTS)
        seen: list[str] = []
        for n in names:
            if n not in seen and n in self._registry:
                seen.append(n)
        limit = 1 if complexity == "moderate" else MAX_CONSULT_SPECIALISTS
        return [self._registry[n] for n in seen[:limit]]

    def consult(self, task: dict) -> ConsultResult:
        # 红旗扫描永远先行,任何复杂度都执行
        safety = self._safety.run(task)
        task = dict(task)
        task["red_flag_level"] = safety.output.get("level")
        complexity = classify_complexity(task)

        outputs: list[AgentResult] = [safety, self._intake.run(task)]
        if complexity in ("moderate", "complex"):
            # 受限会诊: 各 specialist 独立运行(无相互上下文),Manager 合成
            for sp in self._select_specialists(task, complexity):
                outputs.append(sp.run(task))

        merged = self._merge(outputs, task)
        return ConsultResult(
            complexity=complexity, specialist_outputs=outputs, merged=merged
        )

    def fallback(self, task: dict) -> dict:  # Manager 自身的 run() 即 consult 摘要
        return self.consult(task).merged

    @staticmethod
    def _merge(outputs: list[AgentResult], task: dict) -> dict:
        merged: dict = {"escalation": None, "sections": {}}
        for out in outputs:
            merged["sections"][out.agent] = out.output
            if out.agent == "safety_triage" and out.output.get("level"):
                merged["escalation"] = out.output["level"]
        merged["specialists_consulted"] = [o.agent for o in outputs]
        merged["channel"] = "doctor"  # Agent 合成输出仅入医生端,再经 compose 扫描
        return merged
