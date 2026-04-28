from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol


@dataclass
class StreamEvent:
    type: Literal[
        "text_delta", "tool_use_start", "tool_use", "stop", "error"
    ]
    delta: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    reason: str | None = None
    message: str | None = None


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    content: Any  # str | list[block] for tool_use/tool_result


@dataclass
class StreamOptions:
    model: str
    messages: list[ChatMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.3
    tools: list[ToolDef] = field(default_factory=list)


class ILLMProvider(Protocol):
    provider_id: str
    supports_tools: bool

    def stream(self, options: StreamOptions) -> AsyncIterator[StreamEvent]: ...
