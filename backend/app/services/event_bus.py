"""Redis pub/sub event bus for real-time agent streaming (REQ-009 Slice 1).

Architecture §5.1, §8: each analysis run gets a Redis channel (run:{run_id}).
LangGraph nodes publish events via pub/sub; the WebSocket endpoint (Slice 3)
subscribes as an async generator and forwards events to connected clients.

Redis failures are never fatal — a publish failure logs WARNING and returns
silently so a graph run can never crash because Redis is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

import redis.asyncio as redis

from app.schemas.stream import StreamEvent, make_event

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "run:"
CHANNEL_TTL_SECONDS = 86400  # 24 hours


class EventBus:
    """Async Redis pub/sub wrapper for LangGraph event streaming.

    publish_event() is fire-and-forget — it never raises. subscribe_run()
    is an async generator the WebSocket endpoint iterates with async for.
    """

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._publisher: redis.Redis | None = None
        self._subscribers: set[redis.client.PubSub] = set()

    async def connect(self):
        """Called once at app startup (FastAPI lifespan)."""
        self._publisher = redis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=None,
        )

    async def disconnect(self):
        """Called on app shutdown — closes all subscribers and the publisher."""
        for pubsub in list(self._subscribers):
            try:
                await pubsub.close()
            except Exception:
                pass
        self._subscribers.clear()

        if self._publisher:
            try:
                await self._publisher.close()
            except Exception:
                pass
            self._publisher = None

    async def publish_event(
        self,
        run_id: str,
        event: StreamEvent,
    ) -> None:
        """Publish a StreamEvent to the run:{run_id} channel.

        Sets channel TTL to 24h on every publish so orphaned channels
        cannot accumulate. NEVER raises — wraps everything in try/except
        and logs WARNING on failure.
        """
        if self._publisher is None:
            return

        channel = f"{CHANNEL_PREFIX}{run_id}"
        try:
            await self._publisher.publish(channel, event.model_dump_json())
            await self._publisher.expire(channel, CHANNEL_TTL_SECONDS)
        except Exception:
            logger.warning(
                "Failed to publish event run_id=%s event_type=%s",
                run_id,
                event.event_type,
                exc_info=True,
            )

    async def subscribe_run(
        self,
        run_id: str,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Async generator that yields StreamEvent objects as they arrive.

        Caller iterates::

            async for event in event_bus.subscribe_run(run_id):
                ...

        The generator exits when the caller stops iterating (e.g. WebSocket
        disconnects) or when the EventBus is disconnected.
        """
        if self._publisher is None:
            return

        channel = f"{CHANNEL_PREFIX}{run_id}"
        pubsub = self._publisher.pubsub()
        self._subscribers.add(pubsub)

        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message is None:
                    continue
                if message["type"] != "message":
                    continue
                try:
                    event = StreamEvent.model_validate_json(message["data"])
                    yield event
                except Exception:
                    logger.debug(
                        "Failed to parse event from channel %s", channel, exc_info=True
                    )
        except asyncio.CancelledError:
            pass
        finally:
            self._subscribers.discard(pubsub)
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass

    async def publish_heartbeat(self, run_id: str) -> None:
        """Convenience method — publishes a heartbeat event.

        Called by the WebSocket endpoint every 15s.
        """
        await self.publish_event(
            run_id,
            make_event(run_id, "heartbeat", data={"state": "active"}),
        )


# Singleton instance — created at app startup
event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    if event_bus is None:
        raise RuntimeError("EventBus not initialised")
    return event_bus
