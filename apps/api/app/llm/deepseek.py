"""DeepSeek LLM provider — uses OpenAI-compatible chat completions API.

DeepSeek's public API speaks OpenAI-style (messages, tools/tool_calls,
SSE chunks). We adapt to our internal Anthropic-flavored ChatMessage shape
on the way in, and emit our common StreamEvent on the way out.

Why a separate provider class instead of branching inside MiniMaxPlanProvider:
  - Different SDK (openai vs anthropic) — different init, different stream
    object types
  - Different message/tool wire format — converters live where the API does
  - Keeps each provider testable in isolation

Tool-call wire format — the only subtle bit:
  Anthropic:  assistant turn = list of [{type:text}, {type:tool_use,id,name,input}]
              user turn      = list of [{type:tool_result, tool_use_id, content}]
  OpenAI:     assistant turn = {role:assistant, content:str, tool_calls:[{id,function:{name,arguments}}]}
              tool turn      = {role:tool, tool_call_id, content:str}
  We translate during _to_openai_messages() so the agent route can stay
  Anthropic-shaped (it's the more expressive of the two).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.llm.base import ChatMessage, ILLMProvider, StreamEvent, StreamOptions


def _to_openai_messages(messages: list[ChatMessage]) -> list[dict]:
    """Convert our internal ChatMessage[] to OpenAI chat.completions format.

    Notably:
      · An Anthropic 'user' turn carrying tool_result blocks gets split into
        N separate OpenAI 'tool' messages (one per tool_use_id).
      · Plain-text portions of the same turn become a separate 'user' message.
      · An assistant turn with mixed text + tool_use blocks collapses into a
        single OpenAI assistant message with content + tool_calls.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m.content, str):
            out.append({"role": m.role, "content": m.content})
            continue

        # m.content is a list of blocks
        if m.role == "user":
            text_parts: list[str] = []
            for b in m.content:
                btype = b.get("type") if isinstance(b, dict) else None
                if btype == "text":
                    text_parts.append(b.get("text") or "")
                elif btype == "tool_result":
                    content = b.get("content")
                    # OpenAI 'tool' message content must be a string
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False)
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id") or b.get("id") or "",
                        "content": content,
                    })
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})

        elif m.role == "assistant":
            text_parts = []
            tool_calls: list[dict] = []
            for b in m.content:
                btype = b.get("type") if isinstance(b, dict) else None
                if btype == "text":
                    text_parts.append(b.get("text") or "")
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": b.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": b.get("name") or "",
                            "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                        },
                    })
            msg: dict[str, Any] = {"role": "assistant"}
            # OpenAI requires 'content' field; null is allowed when tool_calls present
            msg["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)

        else:
            # Tool messages already converted above; pass-through string content
            out.append({"role": m.role, "content": str(m.content)})

    return out


def _to_openai_tools(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


class DeepSeekProvider(ILLMProvider):
    provider_id = "deepseek"
    supports_tools = True

    def __init__(self, api_key: str, base_url: str, model_name: str):
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        # Same 5-min timeout as MiniMax — DeepSeek reasoning variants can be
        # slow with big system prompts + multi-row tool tasks.
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,
        )
        self._model_name = model_name

    async def stream(self, options: StreamOptions) -> AsyncIterator[StreamEvent]:
        # Build messages: system prompt as a leading 'system' role,
        # then our converted history.
        non_system = [m for m in options.messages if m.role != "system"]
        oai_messages = _to_openai_messages(non_system)
        if options.system_prompt:
            oai_messages = [
                {"role": "system", "content": options.system_prompt}
            ] + oai_messages

        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": oai_messages,
            "max_tokens": options.max_tokens,
            "temperature": min(max(options.temperature, 0.01), 1.0),
            "stream": True,
        }
        if options.tools:
            kwargs["tools"] = _to_openai_tools(options.tools)
            kwargs["tool_choice"] = "auto"

        # tool_call accumulation across delta chunks. OpenAI streams tool
        # calls in fragments — first chunk has id+name, later chunks fill
        # `arguments` JSON piece by piece.
        tool_buffers: dict[int, dict[str, Any]] = {}
        announced_starts: set[int] = set()

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                # Plain text content delta
                if getattr(delta, "content", None):
                    yield StreamEvent(type="text_delta", delta=delta.content)

                # DeepSeek-reasoner sometimes adds reasoning_content; surface
                # as thinking_delta heartbeat (analogous to MiniMax CoT)
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    yield StreamEvent(type="thinking_delta", delta=rc)

                # Tool call deltas
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        idx = tc.index if tc.index is not None else 0
                        buf = tool_buffers.setdefault(
                            idx, {"id": "", "name": "", "args": ""}
                        )
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function:
                            if tc.function.name and not buf["name"]:
                                buf["name"] = tc.function.name
                            if tc.function.arguments:
                                buf["args"] += tc.function.arguments
                        # Once we have id + name, announce the start once.
                        if (
                            idx not in announced_starts
                            and buf["id"] and buf["name"]
                        ):
                            announced_starts.add(idx)
                            yield StreamEvent(
                                type="tool_use_start",
                                id=buf["id"],
                                name=buf["name"],
                            )

                # Finish — flush any tool calls and emit stop reason
                if choice.finish_reason:
                    for idx in sorted(tool_buffers.keys()):
                        buf = tool_buffers[idx]
                        if not buf["name"]:
                            continue
                        try:
                            payload = (
                                json.loads(buf["args"]) if buf["args"].strip() else {}
                            )
                        except json.JSONDecodeError:
                            payload = {}
                        yield StreamEvent(
                            type="tool_use",
                            id=buf["id"],
                            name=buf["name"],
                            input=payload,
                        )
                    tool_buffers.clear()
                    announced_starts.clear()
                    # Map OpenAI finish_reason to our reason field. The agent
                    # route only cares about "tool_calls vs end_turn"-ish so
                    # pass through verbatim.
                    yield StreamEvent(type="stop", reason=choice.finish_reason)

        except Exception as exc:
            yield StreamEvent(type="error", message=str(exc))
