"""Report Assembler node — stub for REQ-003 Slice 1."""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.agents.state import TenderState

logger = logging.getLogger(__name__)


async def report_assembler_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Placeholder final report — real LLM logic in REQ-008."""
    state["final_report"] = "STUB REPORT — REQ-008 pending"
    node_name = "report_assembler"
    logger.info(f"[STUB] {node_name} executed for run {state['run_id']}")
    return state
