Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md
- docs/02_Architecture.md (section 5 — Deployment Topology,
  section 8 — Scaling Considerations)

You are implementing **REQ-009 — Slice 3 (WebSocket Endpoint) only**.

Slices 1 and 2 are already complete. The following is available:
- app/services/event_bus.py → EventBus, get_event_bus(),
  subscribe_run(), publish_heartbeat()
- app/schemas/stream.py → StreamEvent, make_event()
- Redis events being published on "run:{run_id}" channels
  for all graph transitions (verified in Slice 2)
- Existing auth dependency: get_current_company()
  resolves API key from Authorization header

---

## Your scope (do not touch anything outside this list)
- app/api/routers/stream.py (create)
- app/main.py (register the new router — one line only)

---

## What to implement

### 1. app/api/routers/stream.py

  from fastapi import APIRouter, WebSocket, WebSocketDisconnect,
                      Query, Depends, status
  from app.services.event_bus import get_event_bus
  from app.schemas.stream import make_event
  from app.db.models import AnalysisRun, Tender
  import asyncio, json

  router = APIRouter()

  WS_CLOSE_UNAUTHORISED = 4003  # custom close code
  HEARTBEAT_INTERVAL    = 15    # seconds

  @router.websocket("/tenders/{tender_id}/stream")
  async def stream_run_events(
      websocket: WebSocket,
      tender_id: UUID,
      token: str = Query(...,
          description="API key — passed as query param "
          "because browsers cannot set WS headers"),
      db: AsyncSession = Depends(get_db),
  ):

### Step 1 — Authenticate
  Resolve company_id from token (query param, not header).
  Create a helper resolve_company_from_token(token, db)
  that reuses the same hashing/lookup logic as the existing
  REST auth dependency — do not duplicate bcrypt logic.

  If token is invalid:
    await websocket.close(code=WS_CLOSE_UNAUTHORISED)
    return

### Step 2 — Authorise
  Fetch the latest AnalysisRun for this tender_id.
  If tender not found:
    await websocket.close(code=4004)
    return

  If run.company_id != company_id:
    await websocket.close(code=WS_CLOSE_UNAUTHORISED)
    return

### Step 3 — Accept connection
  await websocket.accept()

### Step 4 — Handle already-complete run
  If run.state == "complete":
    # Send complete event immediately and close gracefully
    report = run.agent_trace.get(
        "report_assembler", {}
    ).get("final_report", {})
    await websocket.send_text(make_event(
        str(run.id), "complete",
        data={
            "go_no_go": report.get("go_no_go", "REVIEW"),
            "effective_score": report.get(
                "effective_score", 0.0
            ),
        }
    ).model_dump_json())
    await websocket.close()
    return

  If run.state == "failed":
    await websocket.send_text(make_event(
        str(run.id), "failed",
        data={"error_reason": run.error_reason or "Unknown"}
    ).model_dump_json())
    await websocket.close()
    return

### Step 5 — Stream events + heartbeat
  Use asyncio.gather to run two concurrent tasks:

  Task A — Event forwarder:
    event_bus = get_event_bus()
    async for event in event_bus.subscribe_run(str(run.id)):
        await websocket.send_text(event.model_dump_json())
        # Stop streaming after terminal events
        if event.event_type in ("complete", "failed"):
            break

  Task B — Heartbeat:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        await event_bus.publish_heartbeat(str(run.id))

  Use asyncio.gather with return_when=FIRST_COMPLETED:
  When Task A completes (terminal event received),
  cancel Task B and close the WebSocket.

### Step 6 — Handle disconnect
  Wrap the asyncio.gather in try/except WebSocketDisconnect:
    On disconnect: cancel both tasks, unsubscribe from
    Redis channel. No run impact.

### 2. Register router in app/main.py
  from app.api.routers.stream import router as stream_router
  app.include_router(stream_router)
  (One line addition — no other changes to main.py)

---

## Dependency versions to use
Use Context7 to confirm:
- FastAPI WebSocket API — current method signatures for
  websocket.accept(), websocket.send_text(),
  websocket.close(code=...)
- asyncio.gather() with return_when parameter —
  confirm FIRST_COMPLETED constant location
  (asyncio.FIRST_COMPLETED)

---

## Rules
- Do NOT modify any router other than adding one line
  to main.py.
- Do NOT modify event_bus.py, cost_tracker.py,
  or any node files.
- Do NOT create any frontend files.
- Auth must use the same bcrypt hash comparison as
  existing REST auth — never implement a separate
  token validation logic.
- Close code 4003 for unauthorised — never use
  standard 1008 (policy violation) because that may
  trigger automatic browser reconnect.
- Close code 4004 for not-found (non-standard but
  follows the same pattern as 4003).
- The heartbeat task MUST be cancelled when the event
  forwarder completes — never leave a heartbeat task
  running after the WebSocket closes.
- Never send tender content, financial values, or
  clause text over the WebSocket — events contain
  metadata only.
- The already-complete path (Step 4) must send the
  event AND close — never leave the connection open
  after sending a terminal event.

---

## When you finish
Show me:
1. Full contents of app/api/routers/stream.py
2. Test the WebSocket endpoint with websocat or
   a Python WebSocket client:
   python -c "
   import asyncio, websockets, json

   async def test():
       # First trigger an analysis run and get tender_id
       # Then connect to stream
       uri = 'ws://localhost:8000/tenders/{tender_id}/stream?token={api_key}'
       async with websockets.connect(uri) as ws:
           print('Connected')
           async for msg in ws:
               event = json.loads(msg)
               print(f'{event[\"event_type\"]} — {event.get(\"node_name\", \"\")}')
               if event['event_type'] in ('complete', 'failed',
                                           'awaiting_hitl'):
                   break

   asyncio.run(test())
   "
   Show me the actual event sequence received.
3. Test already-complete run:
   - Use a run that is already in "complete" state
   - Connect WebSocket
   - Assert: receives complete event immediately
   - Assert: connection closes after the event
4. Test unauthorised access:
   - Connect with wrong API key
   - Assert: connection closed with code 4003
   - Assert: no events received
5. Confirm heartbeat task is cancelled after terminal
   event — show me the asyncio.gather cancellation code

Do not move to Slice 4 until I explicitly tell you to.