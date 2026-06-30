"""Supervisor node — validates prerequisites before fanning out to agents."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from app.agents.state import TenderState
from app.agents.tools.profile_lookup import profile_lookup


async def supervisor_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Fetch company profile, validate chunks, and set source languages.

    Raises:
        GraphInterrupt: If the company profile is missing or no chunks exist.
    """
    company_id = state["company_id"]
    tender_id = state["tender_id"]

    # 1. Company profile must exist.
    try:
        await profile_lookup.ainvoke({"company_id": company_id})
    except ValueError:
        state["supervisor_ready"] = False
        raise GraphInterrupt(
            [Interrupt(value="No company profile found.")]
        ) from None

    # 2. Tender must have at least one chunk.
    if not state["chunks"]:
        raise GraphInterrupt(
            [Interrupt(value="No content chunks found for tender.")]
        ) from None

    # 3. Mark ready and collect unique source languages.
    state["supervisor_ready"] = True
    state["source_languages"] = list(
        {chunk["detected_language"] for chunk in state["chunks"]}
    )

    return state
