Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md

You are implementing **REQ-009 — Slice 1 (Event Bus) only**.

This slice produces a standalone Redis pub/sub service —
no WebSocket, no router changes, no frontend files.
Independently testable without any HTTP or WebSocket setup.

---

## Your scope (do not touch anything outside this list)
- app/services/event_bus.py (create)
- app/services/__init__.py (create if not exists)
- app/schemas/stream.py (create — StreamEvent schema)

---

## What to implement

### 1. app/schemas/stream.py

  from pydantic import BaseModel
  from typing import Literal
  from datetime import datetime

  class StreamEvent(BaseModel):
      run_id:     str
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
      node_name:  str | None = None
      timestamp:  str  # ISO 8601
      data:       dict = {}

  def make_event(
      run_id: str,
      event_type: str,
      node_name: str | None = None,
      data: dict = {},
  ) -> StreamEvent:
      """
      Factory function — creates a StreamEvent with
      current timestamp. Use this everywhere instead of
      constructing StreamEvent directly.
      """
      return StreamEvent(
          run_id=run_id,
          event_type=event_type,
          node_name=node_name,
          timestamp=datetime.utcnow().isoformat() + "Z",
          data=data,
      )

### 2. app/services/event_bus.py

  A class-based Redis pub/sub wrapper.
  Use aioredis (or redis.asyncio — check which is
  installed via Context7 for current redis-py version).

  CHANNEL_PREFIX = "run:"
  CHANNEL_TTL_SECONDS = 86400  # 24 hours

  class EventBus:
      def __init__(self, redis_url: str):
          self._redis_url = redis_url
          self._publisher = None   # Redis client for publishing
          self._subscribers = {}   # run_id → pubsub object

      async def connect(self):
          """Called once at app startup (FastAPI lifespan)."""

      async def disconnect(self):
          """Called on app shutdown."""

      async def publish_event(
          self,
          run_id: str,
          event: StreamEvent,
      ) -> None:
          """
          Publish a StreamEvent to the run:{run_id} channel.
          Sets channel TTL to 24h on every publish.
          NEVER raises — wraps everything in try/except.
          On failure: log WARNING with run_id and event_type,
          then return silently.
          """

      async def subscribe_run(
          self,
          run_id: str,
      ) -> AsyncGenerator[StreamEvent, None]:
          """
          Async generator that yields StreamEvent objects
          as they arrive on the run:{run_id} channel.
          Caller iterates: async for event in subscribe_run(run_id)
          Exits when the generator is closed by the caller
          (e.g. WebSocket disconnects).
          """

      async def publish_heartbeat(self, run_id: str) -> None:
          """
          Convenience method — publishes a heartbeat event.
          Called by the WebSocket endpoint every 15s.
          """
          await self.publish_event(
              run_id,
              make_event(run_id, "heartbeat", data={
                  "state": "active"
              })
          )

  # Singleton instance — created at app startup
  event_bus: EventBus | None = None

  def get_event_bus() -> EventBus:
      if event_bus is None:
          raise RuntimeError("EventBus not initialised")
      return event_bus

### 3. Wire into FastAPI lifespan (app/main.py)
  Add to the existing lifespan context manager:

  from app.services.event_bus import EventBus, event_bus
  import app.services.event_bus as event_bus_module

  # On startup:
  event_bus_module.event_bus = EventBus(
      redis_url=settings.REDIS_URL
  )
  await event_bus_module.event_bus.connect()

  # On shutdown:
  if event_bus_module.event_bus:
      await event_bus_module.event_bus.disconnect()

---

## Dependency versions to use
Use Context7 to confirm:
- Current redis-py version and whether to import from
  redis.asyncio or aioredis (these merged — confirm
  the correct import path for the installed version)
- Async pub/sub subscribe() method signature for the
  installed version — the API changed between redis 4.x
  and 5.x

---

## Rules
- Do NOT modify any router, node, or frontend files.
- Do NOT create the WebSocket endpoint — that is Slice 3.
- publish_event() must NEVER raise — silent failure with
  WARNING log only. A Redis failure must never crash
  a graph run.
- subscribe_run() must be an async generator — not a
  callback-based subscription. The WebSocket endpoint
  will iterate it with async for.
- Channel name format: "run:{run_id}" — always this prefix.
- Channel TTL must be set on every publish (not just
  on first publish) — prevents orphaned channels if
  the first publish fails.
- Do NOT include tender content, financial values, or
  clause text in any event data dict — only metadata.

---

## When you finish
Show me:
1. Full contents of app/services/event_bus.py and
   app/schemas/stream.py
2. Confirm publish_event() never raises — show me the
   try/except block
3. Test the event bus independently (no WebSocket needed):
   python -c "
   import asyncio
   from app.services.event_bus import EventBus
   from app.schemas.stream import make_event

   async def test():
       bus = EventBus('redis://localhost:6379')
       await bus.connect()

       # Subscribe in background
       events_received = []
       async def subscriber():
           async for event in bus.subscribe_run('test-run-1'):
               events_received.append(event)
               if len(events_received) >= 2:
                   break

       sub_task = asyncio.create_task(subscriber())
       await asyncio.sleep(0.1)  # let subscriber start

       # Publish 2 events
       await bus.publish_event('test-run-1',
           make_event('test-run-1', 'node_started',
                      node_name='risk_radar'))
       await bus.publish_event('test-run-1',
           make_event('test-run-1', 'node_completed',
                      node_name='risk_radar',
                      data={'duration_ms': 1200}))
       await asyncio.sleep(0.2)  # let events arrive
       await sub_task

       print('Events received:', len(events_received))
       for e in events_received:
           print(f'  {e.event_type} — {e.node_name}')
       await bus.disconnect()

   asyncio.run(test())
   "
   Expected:
     Events received: 2
     node_started — risk_radar
     node_completed — risk_radar
4. Confirm Redis unavailability is handled silently:
   python -c "
   import asyncio
   from app.services.event_bus import EventBus
   from app.schemas.stream import make_event

   async def test():
       # Wrong Redis URL — simulates unavailability
       bus = EventBus('redis://localhost:9999')
       try:
           await bus.connect()
       except Exception:
           pass  # connect may fail — that's OK
       # publish_event must NOT raise
       await bus.publish_event('test-run-2',
           make_event('test-run-2', 'node_started'))
       print('publish_event completed silently — OK')

   asyncio.run(test())
   "
   Expected: prints 'publish_event completed silently — OK'
   with no exception

Do not move to Slice 2 until I explicitly tell you to.