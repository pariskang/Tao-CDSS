"""临床状态契约(协议 L2)。

不变量:
  A. E0/E1 升级一旦设置,只允许保持或由人工降级(由 hermes_loop.state_machine 强制)。
  B. 任一 must_not_miss 为 not_excluded 且未达提问上限时,禁止进入 SUMMARY 相。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 价值驱动选问器的总提问上限(协议 L3.2)
MAX_QUESTIONS = 12

#: 数值越小越危急
ESCALATION_SEVERITY = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}


class EscalationLevel(str, Enum):
    E0_CALL_120 = "E0"
    E1_ER_NOW = "E1"
    E2_SAME_DAY = "E2"
    E3_WITHIN_48H = "E3"
    E4_ROUTINE = "E4"


class Phase(str, Enum):
    CONSENT = "CONSENT"
    IDENTITY_PROXY = "IDENTITY_PROXY"
    CHIEF_COMPLAINT = "CHIEF_COMPLAINT"
    RED_FLAG = "RED_FLAG"
    HPI = "HPI"
    PMH_MED = "PMH_MED"
    SPECIALTY = "SPECIALTY"
    DIFFERENTIAL = "DIFFERENTIAL"
    EVIDENCE = "EVIDENCE"
    VERIFY = "VERIFY"
    DOCTOR_REVIEW = "DOCTOR_REVIEW"
    SUMMARY = "SUMMARY"
    ESCALATION = "ESCALATION"
    PSYCH_RISK = "PSYCH_RISK"
    DEGRADED = "DEGRADED"
    END = "END"


class SourceRef(BaseModel):
    """硬规则5: 任何临床字段必须可溯源。"""

    kind: str = Field(pattern="^(utterance|ehr|doctor_input|tool)$")
    ref_id: str = Field(min_length=1)
    asr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SymptomStatus(str, Enum):
    PRESENT = "present"
    DENIED = "denied"
    PROBABLY_DENIED = "probably_denied"  # hedge 否定,置信上限 0.7,不可排除 must-not-miss
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


class Symptom(BaseModel):
    concept_id: str
    raw_text: str
    status: SymptomStatus
    onset: str | None = None
    severity: str | None = None
    sources: list[SourceRef] = Field(min_length=1)
    secondhand: bool = False  # 代述降权标记


class DifferentialItem(BaseModel):
    condition: str
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = Field(
        default="not_excluded",
        pattern="^(not_excluded|excluded|confirmed_pending)$",
    )
    discriminators_pending: list[str] = []
    evidence_for: list[SourceRef] = []
    evidence_against: list[SourceRef] = []
    guideline_refs: list[str] = []


class Differential(BaseModel):
    must_not_miss: list[DifferentialItem] = []
    most_likely: list[DifferentialItem] = []

    def blocking_items(self) -> list[DifferentialItem]:
        return [i for i in self.must_not_miss if i.status == "not_excluded"]


class ConsentFlags(BaseModel):
    """三项分离同意(协议 L11):科研默认不勾。"""

    recording: bool = False
    retention: bool = False
    research: bool = False


class UtteranceEvent(BaseModel):
    utterance_id: str
    text: str
    asr_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    speaker: str = "patient"
    dialect_region: str | None = None


class ClinicalState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    encounter_id: str
    phase: Phase = Phase.CONSENT
    reporter: str = "self"  # self|parent|caregiver|...
    population: str = "adult"  # adult|pediatric|geriatric|pregnancy
    chief_complaints: list[str] = []
    symptoms: list[Symptom] = []
    medications: list[dict] = []  # 仅经双确认后写入
    allergies: list[dict] = []
    pregnancy: str = "unknown"
    missing_slots: list[str] = []
    asked_slots: list[str] = []
    answered_slots: dict[str, str] = {}
    differential: Differential = Differential()
    escalation: EscalationLevel | None = None
    red_flag_hits: list[str] = []
    question_count: int = 0
    contradictions: list[dict] = []
    degraded_mode: str = "L0"
    schema_version: str = "2.0"

    @field_validator("phase")
    @classmethod
    def _summary_gate(cls, v: Phase, info) -> Phase:
        """不变量B(赋值路径): 在字段写入前拒绝,保证失败赋值不污染状态。

        构造路径由下方 model_validator 兜底(构造时 info.data 不含后续字段)。
        """
        differential = info.data.get("differential")
        question_count = info.data.get("question_count")
        if (
            v == Phase.SUMMARY
            and differential is not None
            and question_count is not None
            and differential.blocking_items()
            and question_count < MAX_QUESTIONS
        ):
            raise ValueError(
                "INVARIANT_B: must_not_miss 未闭环且未达提问上限,禁止进入 SUMMARY"
            )
        return v

    @model_validator(mode="after")
    def _invariants(self) -> "ClinicalState":
        if (
            self.phase == Phase.SUMMARY
            and self.differential.blocking_items()
            and self.question_count < MAX_QUESTIONS
        ):
            raise ValueError(
                "INVARIANT_B: must_not_miss 未闭环且未达提问上限,禁止进入 SUMMARY"
            )
        return self


class CDSSClaim(BaseModel):
    claim_id: str
    text: str
    claim_type: str = Field(
        pattern="^(differential|investigation|risk|education|medication)$"
    )
    output_class: int = Field(ge=0, le=5)
    evidence_bindings: list[dict] = []  # {evidence_id, relation}
    verifier_nli: str = Field(
        default="pending",
        pattern="^(pending|entailed|neutral|contradicted)$",
    )
