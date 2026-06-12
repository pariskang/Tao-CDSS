"""三级降级控制器(协议 L10)。

L0 全功能 → L1 备用小模型仅抽取+模板提问 → L2 纯规则问卷。
红旗筛查、E0/E1 升级、审计写入以规则引擎实现,任何降级层永不失效。
自动降档;升档需人工。降级事件入审计。
"""
from __future__ import annotations

LEVELS = ("L0", "L1", "L2")


class DegradationController:
    def __init__(self, ledger=None, encounter_id: str = "", failure_threshold: int = 3):
        self._ledger = ledger
        self._encounter_id = encounter_id
        self._threshold = failure_threshold
        self._consecutive_failures = 0
        self.level = "L0"

    def record_llm_failure(self, reason: str = "llm_error") -> str:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and self.level != "L2":
            nxt = LEVELS[LEVELS.index(self.level) + 1]
            self._degrade(nxt, reason)
            self._consecutive_failures = 0
        return self.level

    def record_llm_success(self) -> None:
        self._consecutive_failures = 0

    def force_degrade(self, level: str, reason: str) -> None:
        if level not in LEVELS:
            raise ValueError(f"非法降级层: {level}")
        if LEVELS.index(level) <= LEVELS.index(self.level):
            raise ValueError("升档需人工,请使用 restore()")
        self._degrade(level, reason)

    def restore(self, level: str, actor: str) -> None:
        if actor != "human":
            raise PermissionError("升档需人工")
        if level not in LEVELS:
            raise ValueError(f"非法降级层: {level}")
        self.level = level
        self._audit("degradation_restore", {"level": level, "actor": actor})

    def _degrade(self, level: str, reason: str) -> None:
        self.level = level
        self._audit("degradation", {"level": level, "reason": reason})

    def _audit(self, action: str, payload: dict) -> None:
        if self._ledger is not None:
            self._ledger.append(self._encounter_id, "system", action, payload)
