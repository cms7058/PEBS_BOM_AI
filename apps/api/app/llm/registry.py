"""Provider registry — pick a concrete LLM backend at request time.

The chat endpoint passes the user's selected model id (e.g. "MiniMax-M2.7"
or "deepseek-v4-pro") to `get_provider_for_model()`. This file is the
single place that knows how a model id maps to a provider class + the
real model name to forward to that provider.

Adding a new model:
  1. Define an entry in MODEL_REGISTRY below
  2. Make sure the corresponding API key env var is set in config.py
  3. Frontend AgentSidebar dropdown adds the same id

Why model-id routing (not provider-id routing): users think in terms of
'I want to use DeepSeek' or 'I want to use MiniMax', not 'which provider
class wraps that'. UI surfaces model labels, registry maps them to wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.config import settings
from app.llm.base import ILLMProvider
from app.llm.deepseek import DeepSeekProvider
from app.llm.minimax_plan import MiniMaxPlanProvider


@dataclass(frozen=True)
class ModelEntry:
    """A user-selectable LLM option.

    `id`         is what the frontend sends in the chat request.
    `label`      is the display name shown in the model picker.
    `provider`   identifies which provider class handles this model.
    `model_name` is the actual model id forwarded to the provider's API
                 (often equals `id` but kept separate so we can rename UI
                 without breaking existing chat history).
    """
    id: str
    label: str
    provider: str
    model_name: str
    enabled: bool = True


MODEL_REGISTRY: list[ModelEntry] = [
    ModelEntry(
        id="MiniMax-M2.7",
        label="MiniMax M2.7",
        provider="minimaxPlan",
        model_name="MiniMax-M2.7",
    ),
    ModelEntry(
        id="deepseek-v4-pro",
        label="DeepSeek V4 Pro",
        provider="deepseek",
        # Forwarded verbatim to DeepSeek's chat.completions API; override
        # via DEEPSEEK_MODEL env var if their real id differs.
        model_name="",  # filled at runtime from settings.deepseek_model
    ),
]


def list_models() -> list[dict]:
    """Public model catalogue surfaced via /agent/models for the UI."""
    out = []
    for m in MODEL_REGISTRY:
        if not m.enabled:
            continue
        # Only report a model as available if its API key is configured;
        # avoids the frontend showing options that will 500 on first use.
        if m.provider == "minimaxPlan" and not settings.minimax_plan_api_key:
            continue
        if m.provider == "deepseek" and not settings.deepseek_api_key:
            continue
        out.append({
            "id": m.id,
            "label": m.label,
            "provider": m.provider,
        })
    return out


def _get_minimax() -> MiniMaxPlanProvider:
    return _build_minimax()


def _get_deepseek() -> DeepSeekProvider:
    return _build_deepseek()


@lru_cache(maxsize=1)
def _build_minimax() -> MiniMaxPlanProvider:
    return MiniMaxPlanProvider(
        api_key=settings.minimax_plan_api_key,
        base_url=settings.minimax_plan_base_url,
    )


@lru_cache(maxsize=1)
def _build_deepseek() -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model_name=settings.deepseek_model,
    )


def get_provider_for_model(model_id: str | None) -> tuple[ILLMProvider, str]:
    """Return (provider_instance, real_model_name_to_forward).

    If `model_id` is None or unknown, fall back to settings.llm_model.
    Raises ValueError when the resolved provider's API key is missing —
    surface a clear error to the user instead of silently defaulting.
    """
    target_id = model_id or settings.llm_model
    entry = next((m for m in MODEL_REGISTRY if m.id == target_id and m.enabled), None)
    if entry is None:
        # Unknown model — fall back to the configured default.
        entry = next(
            (m for m in MODEL_REGISTRY if m.id == settings.llm_model and m.enabled),
            None,
        )
    if entry is None:
        raise ValueError(f"No usable LLM model registered (asked: {model_id!r})")

    if entry.provider == "minimaxPlan":
        return _get_minimax(), entry.model_name
    if entry.provider == "deepseek":
        return _get_deepseek(), settings.deepseek_model or entry.model_name
    raise ValueError(f"Unsupported provider: {entry.provider}")


# Backwards-compat alias for callers that just want the default provider
# and don't care about model selection (e.g. bom_normalizer at upload time).
@lru_cache(maxsize=1)
def get_provider() -> ILLMProvider:
    provider, _ = get_provider_for_model(settings.llm_model)
    return provider
