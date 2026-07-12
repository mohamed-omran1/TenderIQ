Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md

You are implementing **REQ-009 — Slice 5 (QA) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- app/services/event_bus.py → EventBus with silent failure
- Events published at all graph transitions (Slice 2)
- WS /tenders/{id}/stream?token=<key> endpoint (Slice 3)
- useRunStream hook + 3 updated components (Slice 4)

---

## Your scope (do not touch anything outside this list)
- tests/test_websocket.py (create)
- tests/conftest.py (add fixtures only if not present)

---

## What to implement

A pytest test suite. Uses real Redis (REDIS_URL from env).
Uses real test database (TEST_DATABASE_URL).
Mocks LLM nodes to keep tests fast.
Uses pytest-asyncio throughout.

### Fixtures needed (add to conftest.py if not present)
- redis_client: async Redis client connected to test Redis.
  Yields client, flushes test keys after each test.
- ws_client: an httpx AsyncClient configured for WebSocket
  testing against the FastAPI app. Use
  httpx_ws or starlette.testclient WebSocket support —
  check Context7 for current FastAPI WebSocket testing
  approach.
- active_run: fixture that creates a run in "running"
  state with a valid company + tender. Returns
  { run_id, tender_id, company_api_key }.

### Test cases — implement ALL of the following

# --- EventBus unit tests ---

test_publish_event_sends_to_redis_channel:
  - Create EventBus, connect to test Redis
  - Subscribe to "run:test-123" channel directly via
    redis_client
  - Publish a node_started event via event_bus
  - Assert message received on the channel
  - Assert message is valid JSON matching StreamEvent schema

test_publish_event_silent_on_redis_failure:
  - Create EventBus with invalid Redis URL
  - Call publish_event()
  - Assert no exception raised
  - Assert function returns normally

test_subscribe_run_yields_events:
  - Subscribe to a run channel in background task
  - Publish 3 events sequentially
  - Assert all 3 received in order via subscribe_run()

test_make_event_factory:
  - Call make_event("run-1", "node_started",
                    node_name="risk_radar")
  - Assert event.run_id == "run-1"
  - Assert event.event_type == "node_started"
  - Assert event.node_name == "risk_radar"
  - Assert event.timestamp ends with "Z" (UTC)
  - Assert event.data == {}

test_channel_name_format:
  - Assert EventBus uses "run:{run_id}" format
  - Verify by checking the Redis key after publish:
    keys = await redis_client.keys("run:*")
    assert "run:test-123" in [k.decode() for k in keys]

# --- WebSocket endpoint tests ---

test_ws_accepts_valid_token:
  - Connect to WS with valid company API key
  - Assert connection accepted (no close received)
  - Close connection manually

test_ws_rejects_invalid_token_with_4003:
  - Connect to WS with invalid token
  - Assert WebSocket closes with code 4003
  - Assert no events received before close

test_ws_rejects_wrong_company_with_4003:
  - Create run for company A
  - Connect to WS with company B's API key
  - Assert WebSocket closes with code 4003

test_ws_already_complete_run_sends_complete_and_closes:
  - Create a run in "complete" state with a real report
    in agent_trace
  - Connect WebSocket
  - Assert first (and only) event received is "complete"
  - Assert connection closes after event
  - Assert event data contains go_no_go field

test_ws_already_failed_run_sends_failed_and_closes:
  - Create a run in "failed" state
  - Connect WebSocket
  - Assert event received is "failed"
  - Assert connection closes

test_ws_streams_node_events_in_order:
  - Use active_run fixture (run in "running" state)
  - Connect WebSocket BEFORE triggering analysis
  - Trigger analysis (mock LLM)
  - Collect all events until awaiting_hitl
  - Assert events received in this order:
    node_started(supervisor),
    node_completed(supervisor),
    node_started(risk_radar) OR node_started(scorer)
    OR node_started(financial) [parallel — any order],
    ... all 3 parallel nodes start and complete ...,
    node_started(aggregator),
    node_completed(aggregator),
    awaiting_hitl
  - Assert no event arrives out of order relative to
    the sequential nodes (supervisor before aggregator)

test_ws_awaiting_hitl_event_contains_score_and_count:
  - Collect events until awaiting_hitl
  - Assert awaiting_hitl event data has:
    "feasibility_score": float
    "risk_count": int

test_ws_complete_event_after_hitl_approval:
  - Run to awaiting_hitl, collect events
  - POST /approve
  - Continue collecting events
  - Assert received: resuming, then complete
  - Assert complete event data has go_no_go and
    effective_score fields

test_ws_heartbeat_received:
  - Connect WebSocket
  - Wait 16 seconds (HEARTBEAT_INTERVAL + 1s buffer)
  - Assert at least one "heartbeat" event received
  - Assert heartbeat data has "state" field

test_ws_multiple_clients_same_run:
  - Connect TWO WebSocket clients to the same run channel
  - Publish a test event via event_bus directly
  - Assert BOTH clients receive the event
  - This verifies Redis fan-out works correctly

test_ws_client_disconnect_does_not_affect_run:
  - Connect WebSocket, receive first node_started event
  - Disconnect WebSocket abruptly (close without waiting)
  - Assert analysis run continues and reaches awaiting_hitl
    (poll GET /status to verify)
  - Assert run is NOT stuck or failed due to WS disconnect

# --- Polling fallback verification ---

test_polling_fallback_when_ws_unavailable:
  - This is a documentation test — we cannot easily
    disable WebSocket in a unit test
  - Instead: verify the condition in AgentStreamViewer
    by checking that refetchInterval is set to 2000
    when connectionState="error"
  - Implement as a static code analysis assertion:
    with open('frontend/components/AgentStreamViewer.tsx')
        as f:
        content = f.read()
    assert "connectionState === 'error'" in content
    assert "2000" in content
  - Same pattern for HITLGate.tsx and FullReportView.tsx
  - This confirms the fallback condition is present in code

# --- Security ---

test_ws_no_tender_content_in_events:
  - Run a full analysis to awaiting_hitl via WebSocket
  - Collect all events
  - For each event, assert event.data does not contain:
    "clause_text", "explanation", "annual_turnover",
    "available_bonding_capacity", "amount_value"
  - Only metadata should be in event data

test_ws_cost_update_contains_only_cost_usd:
  - Run analysis (mock LLM with real on_llm_end)
  - Collect cost_update events
  - Assert each cost_update data has only:
    "cost_usd" (float) and "node_name" (from node_name field)
  - Assert no token counts or model names in data dict

# --- Event schema validation ---

test_all_event_types_are_valid_stream_events:
  - For each event_type in the Literal union:
    node_started, node_completed, awaiting_hitl,
    resuming, complete, failed, cost_update, heartbeat
  - Create via make_event() and validate against
    StreamEvent schema
  - Assert StreamEvent(**event.model_dump()) succeeds

test_event_timestamp_is_utc_iso_format:
  - Create an event via make_event()
  - Assert timestamp ends with "Z"
  - Assert datetime.fromisoformat(
      event.timestamp.replace("Z", "+00:00")
    ) does not raise

---

## Rules
- Do NOT modify any router, node, model, schema,
  or frontend files.
- DO use real Redis for all EventBus tests —
  not a mock. Tests require REDIS_URL in env.
- DO use real test database for WebSocket auth tests.
- Every test fully isolated — unique run_id per test,
  flush Redis test keys after each test.
- Use pytest.mark.asyncio for all async tests.
- test_ws_heartbeat_received requires a 16s wait —
  mark it with @pytest.mark.slow and exclude from
  default test run:
  pytest tests/test_websocket.py -v -m "not slow"
  Run separately:
  pytest tests/test_websocket.py -v -m slow
- test_ws_multiple_clients_same_run is the most
  important fan-out test — flag it clearly.
- test_ws_client_disconnect_does_not_affect_run
  verifies the key reliability invariant.

---

## When you finish
Show me:
1. Total test functions created
2. Run the fast suite (no slow tests):
   pytest tests/test_websocket.py -v -m "not slow"
   Show actual terminal output
3. Run the slow suite separately:
   pytest tests/test_websocket.py -v -m slow
   Show actual terminal output
4. Confirm test_ws_multiple_clients_same_run passes —
   show specific output line
5. Confirm test_ws_client_disconnect_does_not_affect_run
   passes — show specific output line
6. Confirm AC coverage — map every Acceptance Criteria
   from REQ-009 to at least one test:
   "AC1 → test_ws_streams_node_events_in_order ✓"

REQ-009 is only complete once all 5 slices pass review.
After REQ-009, only REQ-012 (Evaluation Harness) remains
for MVP completion.
Do not start REQ-012 until I explicitly tell you to.