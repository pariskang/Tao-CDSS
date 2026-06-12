"""LLM 后端抽象: LiteLLMBackend(OpenAI/Anthropic/Gemini/MiniMax 等统一接入)
与 StubLLMBackend(离线确定性,测试/降级用)。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol


class BackendUnavailable(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class LLMBackend(Protocol):
    def complete(self, messages: list[dict], temperature: float = 0.0,
                 max_tokens: int = 1024) -> LLMResult:
        ...  # pragma: no cover


@dataclass
class StubLLMBackend:
    """确定性桩后端: 按脚本顺序返回;脚本耗尽返回 default。"""

    responses: list[str] = field(default_factory=list)
    default: str = '{"verdict": "pass", "problems": []}'
    calls: list[list[dict]] = field(default_factory=list)
    model: str = "stub"

    def complete(self, messages: list[dict], temperature: float = 0.0,
                 max_tokens: int = 1024) -> LLMResult:
        self.calls.append(messages)
        text = self.responses.pop(0) if self.responses else self.default
        return LLMResult(text=text, model=self.model,
                         prompt_tokens=sum(len(m.get("content", "")) for m in messages) // 4,
                         completion_tokens=len(text) // 4)


class LiteLLMBackend:
    """litellm 统一后端: 模型经 HERMES_LLM_MODEL 或构造参数指定。

    例: openai/gpt-4o-mini, anthropic/claude-sonnet-4-6, gemini/gemini-2.5-pro,
        minimax/abab6.5s-chat, ollama/qwen2.5(院内私有化部署)。
    """

    def __init__(self, model: str | None = None, api_base: str | None = None,
                 timeout: float = 30.0):
        try:
            import litellm  # noqa: F401
        except ImportError as e:  # pragma: no cover - 依赖已声明,防御分支
            raise BackendUnavailable("litellm 未安装: pip install litellm") from e
        self._litellm = litellm
        self.model = model or os.environ.get("HERMES_LLM_MODEL", "")
        if not self.model:
            raise BackendUnavailable("未配置模型: 设置 HERMES_LLM_MODEL 或传入 model")
        self._api_base = api_base or os.environ.get("HERMES_LLM_API_BASE")
        self._timeout = timeout

    def complete(self, messages: list[dict], temperature: float = 0.0,
                 max_tokens: int = 1024) -> LLMResult:  # pragma: no cover - 需网络
        kwargs: dict = dict(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, timeout=self._timeout,
        )
        if self._api_base:
            kwargs["api_base"] = self._api_base
        resp = self._litellm.completion(**kwargs)
        usage = getattr(resp, "usage", None)
        cost = 0.0
        try:
            cost = self._litellm.completion_cost(completion_response=resp) or 0.0
        except Exception:
            cost = 0.0
        return LLMResult(
            text=resp.choices[0].message.content or "",
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=cost,
        )
