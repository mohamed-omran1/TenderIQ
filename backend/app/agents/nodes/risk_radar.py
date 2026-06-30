"""Risk Radar agent node — stub for REQ-003 Slice 1."""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.agents.state import TenderState

logger = logging.getLogger(__name__)


async def risk_radar_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Placeholder risk analysis — real LLM logic in REQ-004."""
    node_name = "risk_radar"
    logger.info(f"[STUB] {node_name} executed for run {state['run_id']}")
    return {
        "risk_findings": [
            {
                "category": "stub",
                "severity": "low",
                "clause_text": "STUB",
                "explanation": "Stub — REQ-004 pending",
            }
        ]
    }
