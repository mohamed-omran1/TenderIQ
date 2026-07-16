Read the following documents before writing any code:
- docs/reqs/REQ-003_LangGraph_Analysis_Run.md

You are implementing **REQ-003 — Slice 5 (QA) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- Compiled graph with supervisor, 3 parallel stub nodes,
  aggregator, report_assembler (stub), interrupt_before gate
- POST /tenders/{id}/analyse → launches background graph run
- GET /tenders/{id}/status → polling endpoint
- CostTrackingHandler wired (not yet firing — no real LLM calls
  until REQ-004+)
- GET /analytics/cost → cost breakdown endpoint
- Frontend AgentStreamViewer polling component

---

## Your scope (do not touch anything outside this list)
- tests/test_analysis_run.py (create)
- tests/conftest.py (add fixtures if not already there — do not
  remove existing fixtures from REQ-001/REQ-002)

---

## What to implement

A pytest test suite using httpx.AsyncClient and pytest-asyncio.
Every test case maps directly to an Acceptance Criteria item
from REQ-003.

### Fixtures needed (add to conftest.py if not present)
- ready_tender: fixture that creates a tender with status="ready"
  and at least 3 tender_chunks rows (reuse REQ-001 ingestion fixtures
  if they exist)
- company_with_profile: fixture ensuring a valid company_profiles
  row exists for the test company (reuse REQ-002 fixtures if present)
- company_without_profile: fixture for a company with NO profile
  (for the failure-path test)

### Test cases — implement ALL of the following

# --- Happy path ---

test_analyse_returns_202_with_run_id:
- POST /tenders/{ready_tender.id}/analyse
- Assert HTTP 202
- Assert response contains run_id (valid UUID) and status="pending"
- Assert response time is under 500ms

test_status_reflects_state_transitions:
- POST /analyse
- Poll GET /status immediately — assert state in ("pending", "running")
- Poll GET /status after a short wait (use asyncio.sleep with a
  generous timeout, e.g. 5s, since stub nodes are fast) —
  assert state == "awaiting_hitl"
- Assert agent_trace contains keys for: supervisor, risk_radar,
  scorer, financial, aggregator

test_aggregated_results_merges_all_stub_outputs:
- Run analysis to completion (awaiting_hitl)
- Fetch the analysis_runs row directly from DB
- Assert aggregated_results contains risk_findings, feasibility_score,
  feasibility_breakdown, financial_summary, source_languages
- Assert values match the exact stub placeholders defined in REQ-003

# --- Authorization / validation ---

test_analyse_wrong_company_returns_403:
- Create a tender owned by company_with_profile
- POST /analyse using a DIFFERENT company's API key
- Assert HTTP 403

test_analyse_tender_not_ready_returns_409:
- Create a tender with status="uploading" (not ready)
- POST /analyse
- Assert HTTP 409
- Assert error message contains "not ready"

test_analyse_nonexistent_tender_returns_404:
- POST /tenders/{random_uuid}/analyse
- Assert HTTP 404

test_analyse_duplicate_run_returns_409:
- POST /analyse (first call) — assert 202
- POST /analyse again immediately on the same tender
- Assert second call returns HTTP 409
- Assert error message contains "already in progress"

# --- Failure paths ---

test_analyse_no_company_profile_fails_gracefully:
- Use company_without_profile fixture
- POST /analyse on a ready tender for this company
- Poll GET /status until state == "failed" (with timeout)
- Assert error_reason contains "No company profile found"
- Assert the run never reaches "awaiting_hitl"

test_analyse_empty_chunks_fails_gracefully:
- Create a tender with status="ready" but ZERO tender_chunks rows
- POST /analyse
- Poll GET /status until state == "failed"
- Assert error_reason contains "No content chunks found"

test_status_unknown_tender_returns_404:
- GET /tenders/{random_uuid}/status
- Assert HTTP 404

test_status_wrong_company_returns_403:
- Create a run for company A
- GET /status using company B's API key
- Assert HTTP 403 (not the run data)

# --- Resilience ---

test_checkpoint_survives_simulated_restart:
- POST /analyse and wait until state == "awaiting_hitl"
- Simulate a "restart" by creating a fresh graph instance
  (re-import or re-instantiate the compiled graph object)
- Query the checkpoint directly using the same thread_id (run_id)
- Assert the checkpoint state is still retrievable and intact

# --- Cost tracker wiring (real LLM call not required) ---

test_cost_tracker_handler_fires_on_mock_llm_call:
- Inject a mock LLM call within the test (do not modify any
  node files — use a separate test-only LLM client mock that
  invokes CostTrackingHandler.on_llm_end directly)
- Assert a llm_cost_events row is created with correct run_id
  and node_name
- Assert cost_usd is calculated correctly for the given token counts

test_cost_tracker_never_raises_on_failure:
- Call CostTrackingHandler.on_llm_end with a malformed response
  (missing token_usage key entirely)
- Assert no exception propagates
- Assert the function returns normally (logs the error internally)

test_compute_cost_unknown_model_returns_zero:
- Call compute_cost("totally-made-up-model", {"prompt_tokens": 100,
  "completion_tokens": 50})
- Assert return value is 0.0
- Assert no exception is raised

---

## Rules
- Do NOT modify any router, model, node, graph, or frontend files.
- Do NOT use mocks for the database — use a real test database
  (TEST_DATABASE_URL), consistent with REQ-002's QA approach.
- DO mock the LLM client itself where needed (cost tracker tests) —
  this is different from mocking the database. We mock external
  LLM API calls, never our own database.
- Every test must be fully isolated — no test should depend on
  state left by another test. Clean up analysis_runs and
  llm_cost_events rows created during each test.
- Use pytest.mark.asyncio for all async tests.
- For tests that poll until a state is reached, always use a
  timeout (max 10 seconds) to avoid hanging the test suite —
  fail explicitly with a clear message if the timeout is exceeded,
  rather than hanging indefinitely.

---

## When you finish
Show me:
1. Total number of test functions created
2. Run the full suite and show me the output:
   pytest tests/test_analysis_run.py -v
3. Confirm every Acceptance Criteria item from REQ-003 is covered —
   map them explicitly:
   "AC1 → test_analyse_returns_202_with_run_id ✓"
4. Confirm no test hangs longer than 10 seconds
   (show me the total suite execution time)
5. Confirm test isolation — run with pytest -p no:randomly
   then with pytest-randomly enabled, and confirm all tests pass
   in both orders

REQ-003 is only complete once all 5 slices pass review.
Do not start REQ-004 until I explicitly tell you to.