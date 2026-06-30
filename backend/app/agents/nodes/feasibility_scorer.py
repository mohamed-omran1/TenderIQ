"""Feasibility Scorer agent node — stub for REQ-003 Slice 1."""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.agents.state import TenderState

logger = logging.getLogger(__name__)


async def feasibility_scorer_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Placeholder feasibility scoring — real LLM logic in REQ-005."""
    node_name = "feasibility_scorer"
    logger.info(f"[STUB] {node_name} executed for run {state['run_id']}")
    return {
        "feasibility_score": 0.0,
        "feasibility_breakdown": {"stub": True},
    }
