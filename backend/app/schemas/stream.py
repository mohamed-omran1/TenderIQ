"""Pydantic v2 schema for WebSocket streaming events (REQ-009).

Every event published through the Redis pub/sub bus uses this single typed
schema. Clients receive a stream of JSON objects keyed by run_id so multiple
runs can be watched simultaneously without cross-talk.

No tender content, financial values, or clause text is ever included in the
data dict — only metadata (node_name, scores, counts, durations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StreamEvent(BaseModel):
    """Single event pushed through the Redis pub/sub bus."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    event_type: Literal[
        "node_started",
        "node_completed",
        "awaiting_hitl",
        "resuming",
        "complete",
        "failed",
        "cost_update",
        "heartbeat",
    ]
    node_name: str | None = None
    timestamp: str  # ISO 8601
    data: dict = {}


def make_event(
    run_id: str,
    event_type: str,
    node_name: str | None = None,
    data: dict = {},
) -> StreamEvent:
    """Factory function — creates a StreamEvent with current timestamp.

    Use this everywhere instead of constructing StreamEvent directly so
    timestamps are always consistent and UTC.
    """
    return StreamEvent(
        run_id=run_id,
        event_type=event_type,
        node_name=node_name,
        timestamp=datetime.utcnow().isoformat() + "Z",
        data=data,
    )
