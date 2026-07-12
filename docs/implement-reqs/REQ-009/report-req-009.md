Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md
- Every file you created or modified across Slices 1-5

Generate a structured implementation report for REQ-009.
Do NOT modify any code while generating this report.

---

## Report structure — produce exactly this

### 1. Summary
3 sentences maximum:
  - What REQ-009 delivers (real-time WebSocket streaming
    replacing all polling)
  - The architectural pattern used (Redis pub/sub fan-out,
    event bus service, useRunStream hook)
  - What it means for the MVP (after REQ-009, only
    REQ-012 remains for feature-complete MVP)

### 2. Files Created/Modified — grouped by Slice

  Slice 1 — Event Bus
    app/schemas/stream.py →
      StreamEvent schema, make_event() factory,
      8 event_type literals
    app/services/event_bus.py →
      EventBus class, publish_event() (silent failure),
      subscribe_run() async generator,
      publish_heartbeat(), get_event_bus() singleton
    app/main.py →
      EventBus initialisation in lifespan

  Slice 2 — Backend Publish
    app/api/routers/tenders.py →
      publish calls in run_graph() and _resume_graph()
    app/middleware/cost_tracker.py →
      cost_update publish in on_llm_end()

  Slice 3 — WebSocket Endpoint
    app/api/routers/stream.py →
      WS /tenders/{id}/stream?token=<key>,
      auth via query param, Redis subscription,
      heartbeat task, already-complete handling
    app/main.py →
      stream router registration

  Slice 4 — Frontend
    frontend/hooks/useRunStream.ts →
      WebSocket hook, reconnect backoff, connectionState
    frontend/components/AgentStreamViewer.tsx →
      useRunStream integration, polling fallback gate
    frontend/components/HITLGate.tsx →
      useRunStream for STATE 2, polling fallback gate
    frontend/components/FullReportView.tsx →
      useRunStream for report ready signal, retry gate
    .env.example →
      NEXT_PUBLIC_WS_BASE_URL added

  Slice 5 — QA
    tests/test_websocket.py →
      X test functions across Y categories
    tests/conftest.py →
      Redis fixture, ws_client fixture, active_run fixture

### 3. Acceptance Criteria Verification
Every AC from REQ-009 with actual evidence:

  AC: "WS /tenders/{id}/stream accepts connections and
       streams events in real time"
  Status: ✅ PASS
  Evidence: test_ws_accepts_valid_token — connection
            accepted. test_ws_streams_node_events_in_order
            — events received in correct sequence.

  AC: "AgentStreamViewer receives node events and updates
       without polling — transitions within 100ms"
  Status: ✅ PASS / ⚠️ PARTIAL
  Evidence: (actual test name and result)

  (continue for all 9 ACs)

If any AC NOT fully verified:
  Status: ⚠️ PARTIAL or ❌ NOT VERIFIED
  Reason: specific reason

### 4. Test Coverage Summary
- Total test functions: <number>
- Fast suite (not slow): <number> tests
- Slow suite (slow marker): <number> tests
- Test file: tests/test_websocket.py

- Fast suite output (paste ACTUAL terminal output):
  pytest tests/test_websocket.py -v -m "not slow"

- Slow suite output (paste ACTUAL terminal output):
  pytest tests/test_websocket.py -v -m slow

- Suite execution times (both)
- Breakdown by category:
    EventBus unit tests:          X
    WebSocket endpoint tests:     X
    Event ordering tests:         X
    Security tests:               X
    Schema validation tests:      X
    Polling fallback tests:       X
    Reliability tests:            X

### 5. Event Sequence Verification
Show the ACTUAL Redis output from Slice 2 verification
(the redis-cli subscribe output from a real analysis run):

  run:{run_id} — paste actual sequence here

Then confirm the sequence is correct:
  ☐ node_started(supervisor) before node_completed(supervisor)
  ☐ 3 parallel nodes (risk_radar, scorer, financial)
    all started before any of them completes
  ☐ awaiting_hitl published AFTER db.commit()
  ☐ resuming published at start of _resume_graph()
  ☐ complete published AFTER final db.commit()

### 6. Silent Failure Verification
  Show the ACTUAL output from Slice 1 verification test 4:
  (the "wrong Redis URL" test — confirm publish_event
  completes silently)

  Then confirm:
    ☐ publish_event() wrapped in try/except
    ☐ On failure: logs WARNING only, never raises
    ☐ test_publish_event_silent_on_redis_failure: PASS
    ☐ test_ws_client_disconnect_does_not_affect_run: PASS

### 7. Polling Fallback Verification
  Show the static code analysis results from
  test_polling_fallback_when_ws_unavailable:

  AgentStreamViewer.tsx:
    Contains "connectionState === 'error'": YES/NO
    Contains "2000" (poll interval): YES/NO

  HITLGate.tsx:
    Contains "connectionState === 'error'": YES/NO
    Contains "3000" (poll interval): YES/NO

  FullReportView.tsx:
    Contains "connectionState === 'error'": YES/NO
    Retry gated on connectionState: YES/NO

  All 3 components retain polling as fallback: YES/NO

### 8. useRunStream Hook Assessment
  Show key implementation details:

  a) WebSocket instance management:
     useRef used (not useState): YES/NO
     (show the useRef declaration line)

  b) Close code 4003 handling:
     Reconnection stopped on 4003: YES/NO
     (show the onclose handler code)

  c) Reconnect backoff values:
     attempt 1: Xs, attempt 2: Xs, attempt 3: Xs, cap: Xs
     (show the backoff calculation)

  d) Max reconnect attempts: <number>

### 9. Fan-Out Verification
  test_ws_multiple_clients_same_run:
  Status: PASS / FAIL
  (show specific pytest output line)

  Explain how Redis fan-out works in this implementation:
  - Publisher: event_bus.publish_event() → Redis PUBLISH
  - Channel: "run:{run_id}"
  - Subscribers: each WebSocket connection → separate
    Redis SUBSCRIBE → independent message stream
  - Result: N clients → N subscribers → all receive
    same events simultaneously without server-side fan-out

### 10. Full Pipeline + Streaming Status

  | Feature                    | Status              |
  |----------------------------|---------------------|
  | PDF Ingestor               | ✅ Complete          |
  | Company Profile            | ✅ Complete          |
  | LangGraph Graph            | ✅ Complete          |
  | Risk Radar                 | ✅ Real LLM          |
  | Feasibility Scorer         | ✅ Real LLM          |
  | Financial Analyst          | ✅ Real LLM          |
  | HITL Override Gate         | ✅ Complete          |
  | Report Assembler           | ✅ Real LLM          |
  | WebSocket Streaming        | ✅ Complete          |
  | LLM Cost Tracking          | ✅ Wired + streaming |
  | API Auth + Rate Limit      | ✅ Complete          |
  | Evaluation Harness         | ⏳ REQ-012 — last   |

  One paragraph: what a user experiences now end-to-end
  with real-time streaming — from PDF upload to receiving
  the Go/No-Go report, with live node progress, instant
  HITL notification, and immediate report loading.

### 11. Known Limitations / Deferred Items
  - Heartbeat interval is 15s — some aggressive proxies
    (< 15s timeout) may close the connection. Configurable
    via env variable in v2.
  - useRunStream does not persist events — if the client
    refreshes mid-run, it reconnects and only receives
    future events (historical events not replayed).
  - Redis channel TTL is 24h — if an analyst takes more
    than 24h to review, the channel expires but the
    run checkpoint is still in Postgres (unaffected).
  - Any other limitations identified during implementation

### 12. Dependency Versions Used
  Actual pip list output for:
    redis (or aioredis), fastapi, websockets (if used),
    pytest, pytest-asyncio

  Actual npm list output for:
    (confirm no new npm packages were added — native
    WebSocket API used)

### 13. Risks Carried Forward to REQ-012
  - "REQ-012 (Evaluation Harness) may want to stream
    eval progress via WebSocket — the event bus is
    ready for this with a new event_type if needed"
  - "The cost_update events are being streamed but
    the frontend CostDashboard (REQ-003 Slice 4) still
    uses polling — REQ-012 or a follow-up could wire
    the dashboard to useRunStream for live cost updates"
  - Any other risks noticed during implementation

---

## Rules
- Do NOT modify any code while generating this report.
- Section 5 (Event Sequence) must paste ACTUAL redis-cli
  output — not describe what the sequence should be.
- Section 6 (Silent Failure) must paste ACTUAL terminal
  output from the wrong-Redis-URL test.
- Section 7 (Polling Fallback) must show ACTUAL grep
  or file search results — not assertions.
- Section 8 (useRunStream) must paste ACTUAL code
  snippets — not describe them.
- If pytest has any failures: report honestly — do not
  fix before reporting.
- Output as a single markdown file:
  docs/reports/REQ-009_Implementation_Report.md

---

## After the report is generated
Run this final sanity check and include output under
"Final Sanity Check":

  python -c "
  from app.services.event_bus import EventBus, get_event_bus
  from app.schemas.stream import StreamEvent, make_event

  # Verify StreamEvent schema
  event = make_event('run-test', 'node_started',
                     node_name='risk_radar')
  assert event.run_id == 'run-test'
  assert event.event_type == 'node_started'
  assert event.node_name == 'risk_radar'
  assert event.timestamp.endswith('Z')
  print('StreamEvent schema: OK')

  # Verify all 8 event types are valid
  event_types = [
      'node_started', 'node_completed', 'awaiting_hitl',
      'resuming', 'complete', 'failed',
      'cost_update', 'heartbeat'
  ]
  for et in event_types:
      e = make_event('run-x', et)
      assert e.event_type == et
  print('All 8 event types valid: OK')

  # Verify EventBus class exists and has required methods
  bus = EventBus.__new__(EventBus)
  assert hasattr(bus, 'publish_event')
  assert hasattr(bus, 'subscribe_run')
  assert hasattr(bus, 'publish_heartbeat')
  print('EventBus interface: OK')

  # Verify silent failure — wrong URL
  import asyncio
  async def test_silent():
      b = EventBus('redis://localhost:9999')
      try:
          await b.connect()
      except Exception:
          pass
      await b.publish_event('x', make_event('x', 'heartbeat'))
      print('Silent failure on bad Redis URL: OK')
  asyncio.run(test_silent())
  "

  Expected:
    StreamEvent schema: OK
    All 8 event types valid: OK
    EventBus interface: OK
    Silent failure on bad Redis URL: OK