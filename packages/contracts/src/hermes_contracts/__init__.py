"""hermes_contracts — 全仓库共享的数据契约(宪法级代码)。

任何模块对 clinical_state 的写入都必须通过这里的 Pydantic 校验;
硬规则5: 临床字段写入必须携带 source(SourceRef)。
"""
from hermes_contracts.clinical_state import (
    MAX_QUESTIONS,
    ESCALATION_SEVERITY,
    CDSSClaim,
    ClinicalState,
    ConsentFlags,
    Differential,
    DifferentialItem,
    EscalationLevel,
    Phase,
    SourceRef,
    Symptom,
    SymptomStatus,
    UtteranceEvent,
)
from hermes_contracts.tool_call import ToolCallEnvelope

__all__ = [
    "MAX_QUESTIONS",
    "ESCALATION_SEVERITY",
    "CDSSClaim",
    "ClinicalState",
    "ConsentFlags",
    "Differential",
    "DifferentialItem",
    "EscalationLevel",
    "Phase",
    "SourceRef",
    "Symptom",
    "SymptomStatus",
    "ToolCallEnvelope",
    "UtteranceEvent",
]
