from functools import lru_cache

from app.config import settings
from app.llm.base import ILLMProvider
from app.llm.minimax_plan import MiniMaxPlanProvider


@lru_cache(maxsize=1)
def get_provider() -> ILLMProvider:
    if settings.llm_provider == "minimaxPlan":
        return MiniMaxPlanProvider(
            api_key=settings.minimax_plan_api_key,
            base_url=settings.minimax_plan_base_url,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
