"""HermesAgents(协议 L6)— Manager + bounded specialists。

形态约束:
  - specialist 一律 agents-as-tools,由 Manager 合成最终输出;
  - 受限会诊 ≤3 个 specialist 各自独立输出,禁止自由群聊;
  - 每个 Agent 都有确定性 fallback,LLM 不可用时整体功能不失效;
  - 验证不在本层(单列 HermesGuard),生成与验证分离。
"""
from hermes_agents.base import AgentResult, BaseAgent
from hermes_agents.manager import ManagerAgent, classify_complexity
from hermes_agents.orchestrator import build_task, run_doctor_consult
from hermes_agents.review_panel import PanelVerdict, ReviewPanel
from hermes_agents.specialists import (
    IntakeAgent,
    MedicationSafetyAgent,
    SafetyTriageAgent,
    TCMReasoningAgent,
)

__all__ = [
    "AgentResult",
    "BaseAgent",
    "build_task",
    "IntakeAgent",
    "ManagerAgent",
    "MedicationSafetyAgent",
    "PanelVerdict",
    "run_doctor_consult",
    "ReviewPanel",
    "SafetyTriageAgent",
    "TCMReasoningAgent",
    "classify_complexity",
]
