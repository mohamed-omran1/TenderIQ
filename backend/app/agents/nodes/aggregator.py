"""Results Aggregator node — merges specialist outputs into a single dict."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agents.state import TenderState


async def results_aggregator_node(
    state: TenderState, config: RunnableConfig
) -> TenderState:
    """Merge the three specialist branches into state["aggregated_results"]."""
    state["aggregated_results"] = {
        "risk_findings": state["risk_findings"],
        "feasibility_score": state["feasibility_score"],
        "feasibility_breakdown": state["feasibility_breakdown"],
        "financial_summary": state["financial_summary"],
        "source_languages": state["source_languages"],
    }
    return state
