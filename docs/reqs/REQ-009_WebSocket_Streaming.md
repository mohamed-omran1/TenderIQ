```python
md_content = """# REQ-009
## WebSocket Streaming — Real-Time Agent Events

### Status
| READY FOR IMPLEMENTATION

### Sprint
| Week 3 — HITL + Streaming

### Priority
| P1 — Enhances UX significantly. MVP demo works without it (polling fallback exists) but WebSocket makes the pipeline feel real-time and production-grade.

### Dependencies
| REQ-008 complete (full pipeline working). Redis already in docker-compose (Architecture §5). AgentStreamViewer and HITLGate components exist from REQ-003/007.

### Related Docs
| TenderIQ_Architecture_v1.0 §5.1 (Deployment), §8 (Scaling — Redis pub/sub)  |  TenderIQ_PRD_v1.0 §4.1 (In Scope)

### Owning Component
FastAPI WebSocket
| Redis Pub/Sub
| Frontend Hook

app/api/routers/stream.py
| app/services/event_bus.py
| frontend/hooks/useRunStream.ts

---

### Description
Replace all polling-based progress tracking with a WebSocket streaming endpoint that pushes real-time agent events to connected clients. Currently three components use polling: AgentStreamViewer (2s interval for node progress), HITLGate STATE 2 (3s interval for report completion), and FullReportView (3s retry for report availability). This REQ replaces all three with a single WebSocket connection per run that receives events as they happen.

The backend publishes events to a Redis pub/sub channel keyed by `run_id`. The WebSocket endpoint subscribes to that channel and forwards events to the connected client. This fan-out architecture means multiple clients can watch the same run simultaneously (analyst + manager) without additional server-side code — Redis handles the broadcast.

Polling fallback is preserved — if the WebSocket connection fails or is unavailable, all three components revert to their existing polling behaviour automatically. WebSocket is an enhancement, not a hard dependency.

---

### Preconditions
1. REQ-008 complete — full pipeline working end-to-end.
2. Redis running (already in docker-compose from Architecture §5).
3. AgentStreamViewer, HITLGate, FullReportView components exist from REQ-003/007/008.
4. The `run_graph()` background task in `routers/tenders.py` already iterates over `graph.astream()` events — this is where we add Redis publish calls.

---

### Event Schema
All events follow a single typed schema. The client receives a stream of JSON objects:

```python
class StreamEvent(BaseModel):
    run_id:     str
    event_type: Literal[
        "node_started",    # node began executing
        "node_completed",  # node finished, output available
        "awaiting_hitl",   # graph paused at HITL gate
        "resuming",        # analyst approved, graph resuming
        "complete",        # run finished, report available
        "failed",          # run failed
        "cost_update",     # new LLM cost event logged
        "heartbeat",       # keep-alive ping every 15s
    ]
    node_name:  str | None   # which node fired this event
    timestamp:  str          # ISO 8601
    data:       dict         # event-specific payload

```

#### Event payloads by type

| event_type | data payload |
| --- | --- |
| **node_started** | `{ "node_name": "risk_radar" }` |
| **node_completed** | `{ "node_name": "risk_radar", "duration_ms": 4200 }` |
| **awaiting_hitl** | `{ "feasibility_score": 73.0, "risk_count": 4 }` |
| **resuming** | `{ "action": "approved" | "overridden", "effective_score": 85.0 }` |
| **complete** | `{ "go_no_go": "GO", "effective_score": 85.0 }` |
| **failed** | `{ "error_reason": "..." }` |
| **cost_update** | `{ "node_name": "risk_radar", "cost_usd": 0.0042 }` |
| **heartbeat** | `{ "state": "running" | "awaiting_hitl" }` |

---

### Main Flow

#### Backend — Publishing events

The `run_graph()` background task (`routers/tenders.py`) already iterates over `graph.astream()` events. For each event, publish to Redis before updating the DB:

* A new `event_bus.py` service wraps Redis publish/subscribe with typed methods: `publish_event(run_id, event)` and `subscribe_run(run_id)`.
* Events are published to a Redis channel named `run:{run_id}`.
* After the HITL gate is reached, publish `awaiting_hitl` event. After `_resume_graph()` starts, publish `resuming` event. After run completes, publish `complete` event.
* A heartbeat task publishes a `heartbeat` event every 15 seconds to keep the WebSocket connection alive through proxy timeouts.

#### Backend — WebSocket endpoint

* Client connects to WS `/tenders/{tender_id}/stream` with Authorization token in query param (browsers cannot set custom headers on WS connections — use `?token=<api_key>`).
* FastAPI validates the token and resolves `company_id`. If the tender does not belong to this company: close with `4003` code.
* On successful auth, the endpoint subscribes to the Redis channel `run:{run_id}` for the latest run of this tender.
* For each message received from Redis, forward it as a JSON string to the WebSocket client.
* If the run is already complete when the client connects: immediately send the `complete` event and close the connection gracefully.
* On client disconnect: unsubscribe from Redis channel. No cleanup needed on the run itself.

#### Frontend — useRunStream hook

* A custom React hook `useRunStream(tenderId)` manages the WebSocket lifecycle: connect, receive events, auto-reconnect on disconnect, and return the latest event.
* On mount: open WebSocket to WSS `/tenders/{tenderId}/stream?token={apiKey}`.
* On each message: parse JSON, update local state with the latest `StreamEvent`.
* On disconnect: attempt reconnect with exponential backoff (1s, 2s, 4s, max 30s). Stop reconnecting if `event_type="complete"` or `"failed"` was received.
* On unmount: close WebSocket cleanly.
* Returns: `{ latestEvent, connectionState, error }`.

---

### Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| **WebSocket connection fails (network error)** | `useRunStream` returns `connectionState="error"`. Components fall back to polling automatically. | Polling resumes. No data loss — polling reads the same DB state. |
| **Client connects after run is already complete** | Server sends `complete` event immediately and closes connection gracefully (no need to stream historical events). | Client navigates to report immediately. |
| **Redis is unavailable** | `event_bus.publish_event()` fails silently (try/except, log WARNING). The run continues normally — WebSocket is enhancement not critical path. | Run completes normally. WebSocket clients receive no events but polling fallback works. |
| **Multiple clients watching the same run** | Each client has its own WebSocket connection. Redis fan-out delivers the event to all subscribers automatically. | All clients receive the same events simultaneously. |
| **Proxy/load balancer closes idle connection** | Heartbeat event every 15s keeps the connection alive through standard 30s/60s proxy timeouts. | Connection remains open. |
| **Wrong company token on WebSocket** | Close with WebSocket close code `4003` (custom: unauthorised). Client receives the close event and does not reconnect. | No data exposed to wrong company. |

---

### Postconditions

* Clients watching a run receive `node_started`/`node_completed` events within 100ms of each node transition (WebSocket latency, not polling interval).
* The `awaiting_hitl` event triggers `HITLGate` to transition from loading state to STATE 1 (analyst review) without waiting for a poll cycle.
* The `complete` event triggers `FullReportView` to load the report immediately without waiting for a retry cycle.
* If Redis publish fails: the run completes normally, WebSocket clients receive no events, polling fallback activates. No run data is ever lost due to a WebSocket failure.

---

### Non-Functional Requirements

#### Performance

* WebSocket event latency from Redis publish to client receipt: under 100ms on a local deployment, under 500ms in production (Railway/Vercel).
* Heartbeat interval: 15 seconds — keeps connections alive through standard 30s proxy timeouts.
* Redis channel TTL: 24 hours — channels auto-expire to prevent orphaned subscriptions.

#### Reliability

* WebSocket failures must never affect the analysis pipeline — `event_bus.publish_event()` is always wrapped in try/except.
* Frontend reconnection with exponential backoff (max 30s) — ensures clients recover from transient network issues without user intervention.
* Polling fallback: all three polling components (`AgentStreamViewer`, `HITLGate STATE 2`, `FullReportView`) retain their existing poll logic as fallback when `connectionState="error"`.

#### Security

* API key passed as `?token=` query parameter on WebSocket connection (browsers cannot set Authorization headers on WS). Token validated identically to REST endpoints.
* WebSocket close code `4003` for unauthorised access — clients must not retry after receiving this code.
* No tender content or financial data is included in WebSocket events — only metadata (`node_name`, `state`, `scores`, `counts`).

---

### Implementation Slices

Each slice implemented and reviewed independently.

| Slice | Owns | Scope |
| --- | --- | --- |
| **1. Event Bus** | `app/services/event_bus.py` | Redis pub/sub wrapper: `publish_event(run_id, event)`, `subscribe_run(run_id)`, `unsubscribe_run(run_id)`, `close()`. StreamEvent Pydantic schema. TTL management (24h). Silent failure on Redis unavailability. Independently testable without WebSocket. |
| **2. Backend Publish** | `app/api/routers/tenders.py` (modify `run_graph()` and `_resume_graph()`) | Add `event_bus.publish_event()` calls at: each `graph.astream()` node event (`node_started` + `node_completed`), `awaiting_hitl` transition, `_resume_graph()` start (`resuming`), run completion (`complete`), run failure (`failed`). Also add `cost_update` publish inside `CostTrackingHandler.on_llm_end()`. |
| **3. WS Endpoint** | `app/api/routers/stream.py` (create) | WS `/tenders/{tender_id}/stream?token=<key>` endpoint. Token auth via query param. Company authorisation check. Subscribe to Redis channel. Forward events to client. Handle already-complete runs. Heartbeat task (15s). Clean disconnect. |
| **4. Frontend** | `frontend/hooks/useRunStream.ts` (create), `AgentStreamViewer.tsx` (modify), `HITLGate.tsx` (modify), `FullReportView.tsx` (modify) | `useRunStream` hook with reconnect logic. Update `AgentStreamViewer` to use hook instead of `refetchInterval`. Update `HITLGate STATE 2` to use hook instead of `refetchInterval`. Update `FullReportView` to use hook instead of retry. All three retain polling as fallback when `connectionState="error"`. |
| **5. QA** | `tests/test_websocket.py` | Tests: events received in correct order, `node_started` before `node_completed`, `awaiting_hitl` event fires, `complete` event fires, wrong company receives 4003, Redis unavailability does not affect run, multiple clients receive same events, heartbeat fires every 15s, already-complete run gets immediate `complete` event. |

#### Slice Activation Rule

The project owner selects which slice is executed and when. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope. The agent must not expand scope or select the next slice on its own.

---

### Acceptance Criteria / Definition of Done

* [ ] WS `/tenders/{id}/stream?token=<key>` accepts connections and streams events in real time as the graph executes.
* [ ] `AgentStreamViewer` receives `node_started` and `node_completed` events and updates the UI without polling — node transitions appear within 100ms.
* [ ] `HITLGate` transitions from loading to STATE 1 (analyst review) when `awaiting_hitl` event is received — no poll cycle wait.
* [ ] `FullReportView` receives `complete` event and loads the report immediately — no retry cycle wait.
* [ ] Multiple simultaneous WebSocket clients watching the same run all receive the same events (Redis fan-out verified by test).
* [ ] Wrong company token receives WebSocket close code `4003` and the client does not reconnect.
* [ ] Redis unavailability does not affect the analysis pipeline — run completes normally, components fall back to polling.
* [ ] Heartbeat event fires every 15 seconds to keep connections alive.
* [ ] WebSocket close on already-complete run: client receives `complete` event immediately and connection closes gracefully.

---

### Document Control

After REQ-009, the TenderIQ MVP is feature-complete. REQ-012 (Evaluation Harness) is the only remaining REQ for the initial release. The WebSocket infrastructure introduced here (Redis pub/sub, event schema) also serves as the foundation for future real-time features like collaborative review and live cost dashboards.

