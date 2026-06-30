"""Compiled LangGraph analysis pipeline for TenderIQ (REQ-003 Slice 1)."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.agents.nodes.aggregator import results_aggregator_node
from app.agents.nodes.feasibility_scorer import feasibility_scorer_node
from app.agents.nodes.financial_analyst import financial_analyst_node
from app.agents.nodes.report_assembler import report_assembler_node
from app.agents.nodes.risk_radar import risk_radar_node
from app.agents.nodes.supervisor import supervisor_node
from app.agents.state import TenderState
from app.config import get_settings


class AsyncPostgresCheckpointer(BaseCheckpointSaver):
    """Lazy Postgres checkpointer that creates an AsyncPostgresSaver on first use.

    This wrapper lets us compile the graph at module import time without
    requiring a running event loop or a live database during import. The real
    AsyncPostgresSaver (backed by an AsyncConnectionPool) is created lazily in
    whichever event loop ends up invoking the graph.
    """

    def __init__(self, conn_string: str, *, serde: Any | None = None) -> None:
        super().__init__(serde=serde)
        self._conn_string = conn_string
        self._saver: AsyncPostgresSaver | None = None
        self._pool: AsyncConnectionPool | None = None

    async def _ensure_saver(self) -> AsyncPostgresSaver:
        loop = asyncio.get_running_loop()
        if self._saver is not None and self._saver.loop is loop:
            return self._saver

        # Event loop changed or first use — close any previous pool and recreate.
        if self._pool is not None:
            await self._pool.close()

        self._pool = AsyncConnectionPool(
            self._conn_string,
            max_size=10,
            open=False,
            kwargs={"autocommit": True},
        )
        await self._pool.open()
        self._saver = AsyncPostgresSaver(self._pool)
        await self._saver.setup()
        return self._saver

    async def setup(self) -> None:
        """Ensure checkpoint tables exist; idempotent."""
        saver = await self._ensure_saver()
        await saver.setup()

    async def aget(self, config: RunnableConfig) -> Checkpoint | None:
        saver = await self._ensure_saver()
        return await saver.aget(config)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        saver = await self._ensure_saver()
        return await saver.aget_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        saver = await self._ensure_saver()
        async for item in saver.alist(
            config, filter=filter, before=before, limit=limit
        ):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        saver = await self._ensure_saver()
        return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        saver = await self._ensure_saver()
        await saver.aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        saver = await self._ensure_saver()
        await saver.adelete_thread(thread_id)


def _normalise_psycopg_url(url: str) -> str:
    """Convert SQLAlchemy asyncpg URL to a plain psycopg connection string."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


settings = get_settings()
DATABASE_URL = _normalise_psycopg_url(settings.database_url)

# Compile the graph once at import time and reuse it across all requests.
_builder = StateGraph(TenderState)
_builder.add_node("supervisor", supervisor_node)
_builder.add_node("risk_radar", risk_radar_node)
_builder.add_node("scorer", feasibility_scorer_node)
_builder.add_node("financial", financial_analyst_node)
_builder.add_node("aggregator", results_aggregator_node)
_builder.add_node("report_assembler", report_assembler_node)

_builder.set_entry_point("supervisor")

# Fan-out: supervisor -> 3 parallel specialist branches.
_builder.add_edge("supervisor", "risk_radar")
_builder.add_edge("supervisor", "scorer")
_builder.add_edge("supervisor", "financial")

# Fan-in: all three specialists must finish before aggregation.
_builder.add_edge(["risk_radar", "scorer", "financial"], "aggregator")

# HITL gate pauses before the report assembler.
_builder.add_edge("aggregator", "report_assembler")
_builder.add_edge("report_assembler", END)

graph = _builder.compile(
    checkpointer=AsyncPostgresCheckpointer(DATABASE_URL),
    interrupt_before=["report_assembler"],
)
