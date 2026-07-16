Read the following documents before writing any code:
- docs/reqs/REQ-008_Report_Assembler.md

You are implementing **REQ-008 — Slice 5 (QA) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- app/agents/skills/report_synthesis.py → schemas,
  compute_go_no_go(), FALLBACK_REPORT
- app/agents/nodes/report_assembler.py → real LLM node
- GET /tenders/{id}/report → ReportResponse
- GET /tenders/{id}/status → includes report_available
- FullReportView frontend component

---

## Your scope (do not touch anything outside this list)
- tests/test_report_assembler.py (create)
- tests/conftest.py (add fixtures only if not present —
  do not remove existing fixtures from REQ-001 to REQ-007)

---

## What to implement

A pytest test suite. Mock the LLM — no real API calls.
Real test database (TEST_DATABASE_URL). Consistent with
REQ-004 through REQ-007 QA approach.

### Fixtures needed (add to conftest.py if not present)

- mock_report_llm: returns a valid ReportOutput with:
    go_no_go: "GO"
    effective_score: 82.0
    is_analyst_override: False
    executive_summary: "This tender covers road construction
      in Egypt with a contract value of EGP 35M..."
    recommendation: "We recommend proceeding with a bid
      for this tender."
    risk_summary: [2 items, one high one medium]
    feasibility_highlights: [3 items]
    financial_highlights: [3 items]
    analyst_note: None

- mock_report_llm_override: same as above but:
    go_no_go: "REVIEW"
    effective_score: 65.0
    is_analyst_override: True
    analyst_note: "Feasibility score adjusted from 35
      to 65 by analyst review."

- mock_report_llm_malformed: returns a string that fails
    ReportOutput schema validation

- mock_report_llm_api_error: raises APIConnectionError
    on every call

- complete_run_fixture: creates a full run in "complete"
    state with a real report in agent_trace. Uses
    mock_report_llm and a real HITL approval flow.

### Test cases — implement ALL of the following

# --- effective_score determination ---

test_hitl_override_score_used_when_set:
  - Create state with:
      hitl_override_score = 85.0
      feasibility_score = 40.0
  - Call report_assembler_node
  - Assert result["final_report"]["effective_score"] == 85.0
  - Assert result["final_report"]["is_analyst_override"] == True

test_feasibility_score_used_when_no_override:
  - Create state with:
      hitl_override_score = None
      feasibility_score = 72.0
  - Call report_assembler_node
  - Assert result["final_report"]["effective_score"] == 72.0
  - Assert result["final_report"]["is_analyst_override"] == False

test_override_score_zero_is_valid_not_none:
  - Create state with:
      hitl_override_score = 0.0
      feasibility_score = 75.0
  - Call report_assembler_node
  - Assert result["final_report"]["effective_score"] == 0.0
    NOT 75.0 — the "is not None" check must treat 0.0 as valid
  - Assert result["final_report"]["go_no_go"] == "DECLINE"
    because 0.0 < 40.0

test_is_not_none_check_not_falsy_check:
  - This test documents the contract explicitly
  - For hitl_override_score in [0.0, 0, False]:
    Note: only 0.0 and 0 are valid (float); False is not
    a valid score type — test 0.0 only
  - Assert effective_score == 0.0 in all cases where
    hitl_override_score == 0.0

# --- Go/No-Go computation ---

test_go_no_go_computed_in_python_not_llm:
  - Mock LLM to return go_no_go="GO" in the output
  - Set effective_score=25.0 (should be DECLINE by thresholds)
  - Call report_assembler_node
  - The node must override the LLM's go_no_go with
    Python-computed value
  - Assert result["final_report"]["go_no_go"] == "DECLINE"
    (Python threshold wins, not LLM output)
  Note: this test requires the node to inject go_no_go
  into the LLM context AS INPUT, not ask the LLM to decide.
  The LLM output's go_no_go field should match what was
  given to it — this test verifies the node's compute_go_no_go()
  call happens BEFORE the LLM call and the result is
  passed as context.

test_go_no_go_boundary_go:
  - effective_score = 70.0 → assert go_no_go == "GO"

test_go_no_go_boundary_review_high:
  - effective_score = 69.9 → assert go_no_go == "REVIEW"

test_go_no_go_boundary_review_low:
  - effective_score = 40.0 → assert go_no_go == "REVIEW"

test_go_no_go_boundary_decline:
  - effective_score = 39.9 → assert go_no_go == "DECLINE"

test_go_no_go_zero:
  - effective_score = 0.0 → assert go_no_go == "DECLINE"

test_go_no_go_hundred:
  - effective_score = 100.0 → assert go_no_go == "GO"

# --- compute_go_no_go() pure function ---

test_compute_go_no_go_is_pure_function:
  - Import compute_go_no_go from skill package directly
  - Call 100 times with same input
  - Assert always returns same output (deterministic)
  - Assert no side effects (no DB, no I/O)

# --- Output schema ---

test_final_report_is_always_dict:
  - Call report_assembler_node with mock_report_llm
  - Assert type(result["final_report"]) == dict
  - Assert result["final_report"] is not None
  - Assert result["final_report"] != "STUB REPORT — REQ-008 pending"

test_final_report_has_all_required_keys:
  - Call report_assembler_node
  - Assert these keys exist in final_report:
    go_no_go, effective_score, is_analyst_override,
    executive_summary, recommendation, risk_summary,
    feasibility_highlights, financial_highlights,
    analyst_note

test_risk_summary_max_5_items:
  - Mock LLM to return 7 risk_summary items
    (more than the allowed max 5)
  - If the node enforces the limit: assert len <= 5
  - If schema enforces it via max_length: assert
    schema validation catches it
  - Document which layer enforces this constraint

test_analyst_note_set_when_override:
  - Use mock_report_llm_override fixture
  - Assert analyst_note is not None
  - Assert "adjusted" in analyst_note.lower()

test_analyst_note_null_when_no_override:
  - Use mock_report_llm fixture (no override)
  - Assert analyst_note is None

# --- Error handling ---

test_malformed_output_retries_once_returns_fallback:
  - Use mock_report_llm_malformed
  - Assert LLM called exactly 2 times (initial + 1 retry)
  - Assert result["final_report"]["executive_summary"]
    contains "error" or "failed" (fallback content)
  - Assert result["final_report"]["effective_score"]
    is a float (not 0.0 from FALLBACK_REPORT constant —
    the fallback must have effective_score updated)
  - Assert no exception propagates

test_api_failure_retries_three_times_returns_fallback:
  - Use mock_report_llm_api_error
  - Assert LLM called exactly 3 times
  - Assert result["final_report"] is a dict (fallback)
  - Assert no exception propagates — this node NEVER raises

test_node_never_raises_under_any_condition:
  - Run both error scenarios above
  - Confirm pytest.raises(Exception) is NEVER triggered
  - This test documents the invariant explicitly

test_fallback_has_python_computed_go_no_go:
  - Use mock_report_llm_malformed with effective_score=80.0
  - Assert fallback["go_no_go"] == "GO" (from Python compute)
    NOT "REVIEW" (from FALLBACK_REPORT constant)

# --- Cost tracking ---

test_cost_tracker_fires_on_successful_call:
  - Run report_assembler_node with mock_report_llm
  - Assert one llm_cost_events row with
    node_name="report_assembler"

test_cost_tracker_fires_on_retry_attempts:
  - Use mock_report_llm_malformed (2 LLM calls)
  - Assert exactly 2 llm_cost_events rows with
    node_name="report_assembler"

test_no_cost_event_if_no_llm_called:
  - This scenario doesn't apply to report_assembler
    (it always calls the LLM unless skipped for other reason)
  - Document: "report_assembler always calls LLM —
    no zero-call path exists unlike risk_radar"

# --- Persistence and API ---

test_report_stored_in_agent_trace:
  - Run full pipeline to complete (with real HITL approval,
    mock LLM nodes including report_assembler)
  - Query analysis_runs.agent_trace directly
  - Assert "report_assembler" key exists
  - Assert agent_trace["report_assembler"]["final_report"]
    is a dict with go_no_go field

test_get_report_returns_404_before_complete:
  - Create a run in "awaiting_hitl" state
  - GET /tenders/{id}/report
  - Assert HTTP 404
  - Assert message contains "not yet available"

test_get_report_returns_200_after_complete:
  - Use complete_run_fixture
  - GET /tenders/{id}/report
  - Assert HTTP 200
  - Assert response has all ReportResponse fields
  - Assert go_no_go in ("GO", "REVIEW", "DECLINE")

test_get_report_wrong_company_returns_403:
  - Complete a run for company A
  - GET /report using company B's API key
  - Assert HTTP 403

test_get_report_is_idempotent:
  - Call GET /report on same complete run 3 times
  - Assert all 3 responses are identical
  - Assert no DB writes happen on read

test_report_available_true_in_status_after_complete:
  - Use complete_run_fixture
  - GET /tenders/{id}/status
  - Assert report_available == True
  - Assert state == "complete"

test_report_available_false_before_complete:
  - Create run in "awaiting_hitl" state
  - GET /tenders/{id}/status
  - Assert report_available == False

# --- Security ---

test_financial_values_not_in_logs:
  - Capture log output during report_assembler_node call
  - Assert no financial amount values appear in any log
  - Only metadata (run_id, go_no_go, score, override flag)
    may be logged

---

## Rules
- Do NOT modify any node, router, model, schema, or
  frontend files.
- Do NOT make real LLM API calls.
- DO use a real test database for persistence tests.
- Every test fully isolated — unique run_id, clean DB.
- Use pytest.mark.asyncio for all async tests.
- For pipeline tests waiting for "complete": max 15s timeout.
- test_node_never_raises_under_any_condition is the most
  critical invariant — document it clearly in output.

---

## When you finish
Show me:
1. Total test functions created
2. Run the full suite:
   pytest tests/test_report_assembler.py -v
   Show actual terminal output
3. Confirm test_override_score_zero_is_valid_not_none
   passes — show specific output line
4. Confirm test_go_no_go_computed_in_python_not_llm
   passes — show specific output line
5. Confirm test_node_never_raises_under_any_condition
   passes — show specific output line
6. Confirm AC coverage — map every Acceptance Criteria
   from REQ-008 to at least one test:
   "AC1 → test_hitl_override_score_used_when_set ✓"

REQ-008 is only complete once all 5 slices pass review.
After REQ-008 is complete, TenderIQ has a full MVP
demo-able pipeline. Do not start REQ-009 until I
explicitly tell you to.