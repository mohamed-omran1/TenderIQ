"""Cost-tracking callback handler for LangGraph LLM calls (REQ-003 Slice 3).

CostTrackingHandler is a LangChain BaseCallbackHandler attached to every LLM
client via the `callbacks` config parameter. It intercepts `on_llm_end` events
and writes one llm_cost_events row per call.

Rules (from imp-slice-03):
  - on_llm_end NEVER raises into the graph — the entire body is wrapped in
    try/except and errors are logged silently. A cost-logging failure must
    never crash an analysis run.
  - compute_cost is a pure function: no DB, no I/O, independently testable.
  - Unknown model names return 0.0 — never raise KeyError or ValueError.
  - cost_usd, input_tokens, output_tokens NEVER appear in application logs
    at DEBUG or INFO level — only in the DB.

Pricing verified against OpenAI and Anthropic official pricing pages (2026-06).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LlmCostEvent

logger = logging.getLogger(__name__)

_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}


def compute_cost(model: str, usage: dict[str, Any]) -> float:
    """Compute USD cost from model name and token counts. Pure function.

    Args:
        model: Model identifier string (e.g. "gpt-4o", "claude-sonnet-4-6").
        usage: Dict with "prompt_tokens" and "completion_tokens" keys.

    Returns:
        Cost in USD. Returns 0.0 for unknown models — never raises.
    """
    pricing = _MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0

    input_per_1m, output_per_1m = pricing
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    return (prompt_tokens / 1_000_000 * input_per_1m) + (
        completion_tokens / 1_000_000 * output_per_1m
    )


class CostTrackingHandler(BaseCallbackHandler):
    """LangChain callback that persists per-LLM-call cost events to Postgres."""

    def __init__(self, run_id: str, node_name: str, db: AsyncSession) -> None:
        self.run_id = run_id
        self.node_name = node_name
        self.db = db

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            llm_output = response.llm_output or {}
            usage = llm_output.get("token_usage", {})
            model = llm_output.get("model_name", "unknown")
            cost_usd = compute_cost(model, usage)

            await self.db.execute(
                insert(LlmCostEvent).values(
                    run_id=self.run_id,
                    node_name=self.node_name,
                    model=model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    cost_usd=cost_usd,
                )
            )
            await self.db.commit()
        except Exception:
            logger.warning(
                "cost_tracking_failed run_id=%s node_name=%s",
                self.run_id,
                self.node_name,
            )
