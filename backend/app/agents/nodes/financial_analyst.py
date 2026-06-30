"""Financial Analyst agent node — stub for REQ-003 Slice 1."""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.agents.state import TenderState

logger = logging.getLogger(__name__)


async def financial_analyst_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Placeholder financial analysis — real LLM logic in REQ-006."""
    node_name = "financial_analyst"
    logger.info(f"[STUB] {node_name} executed for run {state['run_id']}")
    return {
        "financial_summary": {
            "stub": True,
            "bonds": [],
            "commitments": [],
        }
    }
