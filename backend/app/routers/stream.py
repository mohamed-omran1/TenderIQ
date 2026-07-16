"""WebSocket streaming endpoint — real-time agent events (REQ-009 Slice 3).

Provides WS /tenders/{tender_id}/stream?token=<api_key> for clients to receive
StreamEvents as the LangGraph analysis pipeline executes. Auth via query-param
API key (browsers cannot set headers on WS connections), reusing the same
bcrypt verification logic as REST endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisRun, Company
from app.db.session import get_session
from app.middleware.auth import _verify
from app.schemas.stream import make_event
from app.services.event_bus import get_event_bus

logger = logging.getLogger(__name__)

router = APIRouter()

WS_CLOSE_UNAUTHORISED = 4003
WS_CLOSE_NOT_FOUND = 4004
HEARTBEAT_INTERVAL = 15


async def resolve_company_from_token(token: str, db: AsyncSession) -> Company | None:
    result = await db.execute(select(Company))
    for company in result.scalars():
        if _verify(token, company.api_key_hash):
            return company
    return None


@router.websocket("/tenders/{tender_id}/stream")
async def stream_run_events(
    websocket: WebSocket,
    tender_id: UUID,
    token: str = Query(
        ...,
        description="API key — passed as query param because browsers cannot set WS headers",
    ),
    db: AsyncSession = Depends(get_session),
):
    # Step 1 — Authenticate
    company = await resolve_company_from_token(token, db)
    if company is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORISED)
        return

    # Step 2 — Authorise
    result = await db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.tender_id == str(tender_id))
        .order_by(AnalysisRun.started_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()

    if run is None:
        await websocket.close(code=WS_CLOSE_NOT_FOUND)
        return

    if run.company_id != company.id:
        await websocket.close(code=WS_CLOSE_UNAUTHORISED)
        return

    # Step 3 — Accept connection
    await websocket.accept()

    # Step 4 — Handle already-complete or failed run
    if run.state == "complete":
        report = run.agent_trace.get("report_assembler", {}).get("final_report", {})
        await websocket.send_text(
            make_event(
                str(run.id),
                "complete",
                data={
                    "go_no_go": report.get("go_no_go", "REVIEW"),
                    "effective_score": report.get("effective_score", 0.0),
                },
            ).model_dump_json()
        )
        await websocket.close()
        return

    if run.state == "failed":
        await websocket.send_text(
            make_event(
                str(run.id),
                "failed",
                data={"error_reason": run.error_reason or "Unknown"},
            ).model_dump_json()
        )
        await websocket.close()
        return

    # Step 5 — Stream events + heartbeat
    event_bus = get_event_bus()
    forwarder_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None

    async def forward_events():
        async for event in event_bus.subscribe_run(str(run.id)):
            await websocket.send_text(event.model_dump_json())
            if event.event_type in ("complete", "failed"):
                break

    async def heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await event_bus.publish_heartbeat(str(run.id))

    # Step 6 — Handle disconnect
    try:
        forwarder_task = asyncio.create_task(forward_events())
        heartbeat_task = asyncio.create_task(heartbeat())

        done, pending = await asyncio.wait(
            [forwarder_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        for t in (forwarder_task, heartbeat_task):
            if t is not None and not t.done():
                t.cancel()
