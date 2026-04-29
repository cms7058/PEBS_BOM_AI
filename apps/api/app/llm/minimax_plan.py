"""
MiniMax Token Plan provider.

Token Plan is MiniMax's subscription tier; it exposes an Anthropic-compatible
endpoint at https://api.minimaxi.com/anthropic and uses a key distinct from
the pay-as-you-go API key.

Supported models: MiniMax-M2.7, MiniMax-M2.7-highspeed, MiniMax-M2.5(-highspeed),
MiniMax-M2.1(-highspeed), MiniMax-M2.

Since the endpoint is Anthropic-compatible we reuse the Anthropic SDK.

Docs: https://platform.minimaxi.com/docs/token-plan/intro
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.llm.base import ChatMessage, ILLMProvider, StreamEvent, StreamOptions, ToolDef


def _to_anthropic_message(msg: ChatMessage) -> dict[str, Any]:
    # `content` can be a string or a pre-built list of content blocks
    # (used for tool_use / tool_result round-trips).
    return {"role": msg.role, "content": msg.content}


def _to_anthropic_tool(tool: ToolDef) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


class MiniMaxPlanProvider(ILLMProvider):
    provider_id = "minimaxPlan"
    supports_tools = True

    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/anthropic"):
        if not api_key:
            raise ValueError("MINIMAX_PLAN_API_KEY is required")
        # MiniMax-M2.7 emits chain-of-thought before the final answer; for
        # multi-row reasoning tasks (e.g. parsing a pasted AVL table into
        # brand_bulk_add) the thinking phase alone can exceed the SDK's
        # default 60s read timeout. Bump to 5 minutes — well past the
        # observed worst-case while still bounded enough that hung requests
        # surface.
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,
        )

    async def stream(self, options: StreamOptions) -> AsyncIterator[StreamEvent]:
        # Token Plan docs: temperature must be in (0, 1]. Clamp defensively.
        temp = min(max(options.temperature, 0.01), 1.0)

        messages = [_to_anthropic_message(m) for m in options.messages if m.role != "system"]
        tools = [_to_anthropic_tool(t) for t in options.tools] if options.tools else None

        kwargs: dict[str, Any] = {
            "model": options.model,
            "max_tokens": options.max_tokens,
            "temperature": temp,
            "messages": messages,
        }
        if options.system_prompt:
            kwargs["system"] = options.system_prompt
        if tools:
            kwargs["tools"] = tools

        tool_buffers: dict[int, dict[str, Any]] = {}

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)

                    if et == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_buffers[event.index] = {
                                "id": block.id,
                                "name": block.name,
                                "json": "",
                            }
                            yield StreamEvent(
                                type="tool_use_start", id=block.id, name=block.name
                            )

                    elif et == "content_block_delta":
                        delta = event.delta
                        dt = getattr(delta, "type", None)
                        if dt == "text_delta":
                            yield StreamEvent(type="text_delta", delta=delta.text)
                        elif dt == "input_json_delta":
                            buf = tool_buffers.get(event.index)
                            if buf is not None:
                                buf["json"] += delta.partial_json
                        elif dt == "thinking_delta":
                            # MiniMax-M2.7 chain-of-thought. Forward as a
                            # heartbeat so the UI sees motion during long
                            # reasoning. The text content itself is a
                            # rolling preview; route handles aggregation.
                            yield StreamEvent(
                                type="thinking_delta",
                                delta=getattr(delta, "thinking", "") or "",
                            )

                    elif et == "content_block_stop":
                        buf = tool_buffers.pop(event.index, None)
                        if buf:
                            import json

                            payload: dict[str, Any] = {}
                            if buf["json"].strip():
                                try:
                                    payload = json.loads(buf["json"])
                                except json.JSONDecodeError:
                                    payload = {}
                            yield StreamEvent(
                                type="tool_use",
                                id=buf["id"],
                                name=buf["name"],
                                input=payload,
                            )

                    elif et == "message_delta":
                        stop_reason = getattr(event.delta, "stop_reason", None)
                        if stop_reason:
                            yield StreamEvent(type="stop", reason=stop_reason)

        except Exception as exc:
            yield StreamEvent(type="error", message=str(exc))

    async def complete_json(
        self, system_prompt: str, user_prompt: str, model: str, max_tokens: int = 4096
    ) -> str:
        """One-shot, non-streaming completion. Used by the BOM normalizer."""
        resp = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Concatenate text blocks (skip any thinking blocks).
        parts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)
