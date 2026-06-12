"""工具调用信封(协议 L5)。HermesBroker 对其做 schema 校验。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ToolCallEnvelope(BaseModel):
    tool_call_id: str
    encounter_id: str
    agent: str
    skill: str
    tool: str = Field(min_length=1)
    input: dict = {}
    idempotency_key: str | None = None  # 写操作必填(broker 校验)
    policy: dict = {}  # {patient_direct_allowed, risk_level}
    trace: dict = {}  # {source_utterances, asr_confidence_min}
