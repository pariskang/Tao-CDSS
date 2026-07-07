"""医生端会诊编排 — ManagerAgent 接入主链路(外部审计 P0)。

运行图(协议 L6):
  LoopEngine 完成病史采集(DOCTOR_REVIEW)
    → build_task(结构化任务,含溯源文本)
    → ManagerAgent.classify_complexity 路由
    → SafetyTriage / Intake / MedicationSafety / TCMReasoning 受限会诊
    → agent_consult 段并入 compose_doctor_output,供医生采纳/修改/拒绝
    → 会诊摘要写审计哈希链

安全边界: 本层输出仅入医生通道;LLM 不可用时各 specialist 走确定性
fallback,主链路永不阻断;红旗判级仍以引擎的规则扫描为准,本层的
safety_triage 输出只作交叉核对展示。
"""
from __future__ import annotations

from hermes_agents.manager import ManagerAgent

#: skill → 专科路由键(SPECIALTY_MAP 的键;PENDING: 按院内科室表扩充)
SKILL_SPECIALTY = {
    "oncology_bone_metastasis": "oncology",
}


def build_task(state, texts: list[str], skill_name: str | None = None,
               tcm_requested: bool = False) -> dict:
    """从 clinical_state 构造结构化会诊任务(替代自由 dict 的第一步)。"""
    return {
        "encounter_id": state.encounter_id,
        "texts": list(texts),
        "chief_complaints": list(state.chief_complaints),
        "symptoms": [
            {"concept_id": s.concept_id, "status": s.status.value}
            for s in state.symptoms
        ],
        "medications": [
            m.get("name") for m in state.medications
            if isinstance(m, dict) and m.get("name")
        ],
        "allergies": [
            a.get("name") for a in state.allergies
            if isinstance(a, dict) and a.get("name")
        ],
        "comorbidities": [],
        "specialty": SKILL_SPECIALTY.get(skill_name or ""),
        "tcm_requested": tcm_requested,
        "unexcluded": [
            i.condition for i in state.differential.blocking_items()
        ],
    }


def run_doctor_consult(
    engine, gateway=None, manager: ManagerAgent | None = None,
    tcm_requested: bool = False,
) -> dict:
    """对已完成采集的 encounter 执行受限会诊,返回医生端 agent_consult 段。"""
    manager = manager or ManagerAgent(gateway)
    skill_name = engine.skill.name if engine.skill else None
    task = build_task(engine.state, engine.texts, skill_name=skill_name,
                      tcm_requested=tcm_requested)
    result = manager.consult(task)
    consult = {
        "channel": "doctor",
        "complexity": result.complexity,
        "specialists_consulted": result.merged["specialists_consulted"],
        "escalation_crosscheck": result.merged.get("escalation"),
        "sections": result.merged["sections"],
        "used_llm": {
            o.agent: o.used_llm for o in result.specialist_outputs
        },
        "notes": [n for o in result.specialist_outputs for n in o.notes],
        "disclaimer": "会诊结论仅供医生参考,须逐条采纳/修改/拒绝并留痕",
    }
    engine.ledger.append(
        engine.encounter_id,
        "manager_agent",
        "agent_consult",
        {
            "complexity": result.complexity,
            "specialists": consult["specialists_consulted"],
            "used_llm": consult["used_llm"],
            "escalation_crosscheck": consult["escalation_crosscheck"],
            "severity": "INFO",
        },
    )
    return consult
