"""ASR 客户端抽象:先桩后真(坑6: Stub 先行,语音是最后接入的可替换件)。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class ASRResult:
    text: str
    confidence: float
    utterance_id: str
    final: bool = True


class ASRClient(Protocol):
    def stream(self, hotwords: list[str] | None = None) -> Iterator[ASRResult]:
        ...  # pragma: no cover


class StubASRClient:
    """Sprint1-3 用:直接喂文本脚本,使全链路先于语音栈跑通(垂直切片)。"""

    def __init__(self, script: list):
        # script 元素: "文本" 或 ("文本", 置信度)
        self._script = script

    def stream(self, hotwords: list[str] | None = None) -> Iterator[ASRResult]:
        for i, item in enumerate(self._script):
            if isinstance(item, tuple):
                text, conf = item
            else:
                text, conf = item, 0.95
            yield ASRResult(
                text=text, confidence=conf, utterance_id=f"utt_{i:03d}"
            )


class FireRedASRClient:  # pragma: no cover - 真实现对接自托管服务,Sprint 6
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def stream(self, hotwords: list[str] | None = None) -> Iterator[ASRResult]:
        raise NotImplementedError("FireRedASR 对接在 Sprint 6 实现")
