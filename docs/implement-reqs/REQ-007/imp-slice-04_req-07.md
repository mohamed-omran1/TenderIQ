Read the following documents before writing any code:
- docs/reqs/REQ-007_HITL_Override_Gate.md

You are implementing **REQ-007 — Slice 4 (QA) only**.

Slices 1, 2, and 3 are already complete and working:
- POST /tenders/{id}/approve → HTTP 202, creates hitl_overrides
  row with action="approved", resumes graph
- POST /tenders/{id}/override → HTTP 202, creates hitl_overrides
  row with action="overridden", injects hitl_override_score
  into checkpoint, resumes graph
- GET /tenders/{id}/hitl-override → returns override record
  (without justification)
- HITLGate.tsx → 3 states (awaiting_hitl, resuming, complete)
  with needs_review banner and override history display

---

## Your scope (do not touch anything outside this list)
- tests/test_hitl.py (create)
- tests/conftest.py (add fixtures only if not already present —
  do not remove any existing fixtures from REQ-001 through REQ-006)

---

## What to implement

A pytest test suite using pytest-asyncio and a real test database
(TEST_DATABASE_URL). Use mock LLM for report_assembler node
(which runs after HITL approval). Consistent with REQ-003
through REQ-006 QA approach.

### Fixtures needed (add to conftest.py if not present)
- awaiting_hitl_run: fixture that creates a full analysis run
  in "awaiting_hitl" state — runs the graph with mock LLM
  nodes (REQ-004/005/006 nodes mocked) and waits until
  state = "awaiting_hitl". Returns the run_id and tender_id.
- mock_report_assembler: patches the report_assembler_node
  to set state["final_report"] = "MOCK REPORT" and return
  immediately — prevents real LLM calls during HITL tests.
- second_company: a fixture for a second company with its
  own API key (for cross-tenant authorisation tests).

### Test cases — implement ALL of the following

# --- Flow A: Approve as-is ---

test_approve_returns_202_with_hitl_response:
  - Use awaiting_hitl_run fixture
  - POST /tenders/{id}/approve
  - Assert HTTP 202
  - Assert response has: run_id, action="approved",
    original_score (float), overridden_score=null, message
  - Assert original_score matches analysis_runs.feasibility_score

test_approve_creates_immutable_hitl_overrides_row:
  - POST /approve
  - Query DB: SELECT * FROM hitl_overrides WHERE run_id=X
  - Assert exactly 1 row exists
  - Assert action="approved"
  - Assert original_score matches analysis_runs.feasibility_score
  - Assert overridden_score IS NULL
  - Assert created_at is not null

test_approve_transitions_run_to_complete:
  - POST /approve
  - Poll GET /status until state="complete" (max 15s timeout)
  - Assert final state = "complete"
  - Assert completed_at is not null in analysis_runs

test_approve_report_assembler_ran:
  - POST /approve
  - Wait for state="complete"
  - Query analysis_runs.agent_trace directly
  - Assert "report_assembler" key exists in agent_trace
  - This confirms the graph actually resumed and completed

test_hitl_approved_injected_into_checkpoint:
  - POST /approve
  - Immediately read checkpoint (before graph completes):
    state = await graph.aget_state(config)
    assert state.values["hitl_approved"] == True
    assert state.values["hitl_override_score"] is None

# --- Flow B: Override score ---

test_override_returns_202_with_correct_scores:
  - POST /tenders/{id}/override with overridden_score=85.0
  - Assert HTTP 202
  - Assert response action="overridden"
  - Assert response original_score matches AI score
  - Assert response overridden_score=85.0

test_override_creates_hitl_overrides_row_with_scores:
  - POST /override with overridden_score=30.0
  - Query hitl_overrides
  - Assert action="overridden"
  - Assert overridden_score=30.0
  - Assert original_score != 30.0 (they must differ)
  - Assert justification is stored in DB (not null)

test_override_injects_score_into_checkpoint:
  - POST /override with overridden_score=72.5
  - Read checkpoint immediately after:
    state = await graph.aget_state(config)
    assert state.values["hitl_override_score"] == 72.5
    assert state.values["hitl_approved"] == True

test_override_score_used_in_final_report:
  - POST /override with overridden_score=90.0
  - Wait for state="complete"
  - This test documents the CONTRACT that REQ-008 must
    honour: hitl_override_score=90.0 must be in the
    checkpoint when report_assembler runs.
  - Assert in agent_trace that report_assembler ran
  - (Full validation of report content is REQ-008's job)

# --- Boundary values ---

test_override_minimum_score_zero:
  - POST /override with overridden_score=0.0
  - Assert HTTP 202 (valid)
  - Assert hitl_overrides.overridden_score=0.0

test_override_maximum_score_hundred:
  - POST /override with overridden_score=100.0
  - Assert HTTP 202 (valid)

test_override_score_below_zero_returns_422:
  - POST /override with overridden_score=-1.0
  - Assert HTTP 422

test_override_score_above_hundred_returns_422:
  - POST /override with overridden_score=100.1
  - Assert HTTP 422

test_override_justification_too_short_returns_422:
  - POST /override with justification="too short"
    (less than 10 characters)
  - Assert HTTP 422

test_override_missing_justification_returns_422:
  - POST /override with no justification field
  - Assert HTTP 422

# --- Authorisation ---

test_approve_wrong_company_returns_403:
  - Create run for company A
  - POST /approve using second_company API key
  - Assert HTTP 403

test_override_wrong_company_returns_403:
  - Same pattern as above for /override

test_approve_nonexistent_tender_returns_404:
  - POST /tenders/{random_uuid}/approve
  - Assert HTTP 404

# --- State validation ---

test_approve_run_not_in_awaiting_hitl_returns_409:
  - Create a run in "running" state (not yet awaiting_hitl)
  - POST /approve
  - Assert HTTP 409
  - Assert error message contains "awaiting review"

test_approve_already_complete_run_returns_409:
  - Approve a run (→ complete)
  - POST /approve again on the same run
  - Assert HTTP 409

test_double_approve_race_condition:
  - Fire two POST /approve requests concurrently
    (use asyncio.gather)
  - Assert exactly one returns HTTP 202
  - Assert the other returns HTTP 409
  - Assert exactly ONE hitl_overrides row exists
    (not two — the "resuming" intermediate state prevents this)

# --- Audit log immutability ---

test_hitl_overrides_row_cannot_be_updated:
  - Create a hitl_overrides row via approve
  - Attempt direct SQLAlchemy UPDATE on the row:
    await db.execute(update(HITLOverride)
        .where(HITLOverride.run_id == run_id)
        .values(action="overridden"))
  - Assert the application layer has no such code path
    (this test documents the constraint, not enforces it
    at DB level — verify by searching routers/tenders.py
    for any update() call on hitl_overrides)
  - Assert grep of routers/tenders.py returns zero
    update() calls on HITLOverride model

test_hitl_overrides_row_preserved_on_resume_failure:
  - Create an awaiting_hitl run
  - Patch _resume_graph to raise an Exception after
    writing hitl_overrides but before graph resumes
  - POST /approve
  - Assert analysis_runs.state = "failed"
  - Assert hitl_overrides row still exists (not rolled back)
  - Assert hitl_overrides.action = "approved"

# --- GET /hitl-override endpoint ---

test_get_hitl_override_returns_record_after_approve:
  - POST /approve
  - GET /tenders/{id}/hitl-override
  - Assert HTTP 200
  - Assert response has: run_id, action, original_score,
    overridden_score, created_at
  - Assert "justification" is NOT in response body
    (or is null — never the actual justification text)

test_get_hitl_override_returns_404_before_hitl:
  - Create a run in "awaiting_hitl" (not yet approved)
  - GET /tenders/{id}/hitl-override
  - Assert HTTP 404

test_get_hitl_override_wrong_company_returns_403:
  - Approve a run for company A
  - GET /hitl-override using second_company key
  - Assert HTTP 403

# --- Security ---

test_justification_never_in_api_response:
  - POST /override with justification="This is secret info"
  - GET /tenders/{id}/hitl-override
  - Assert "This is secret info" does not appear anywhere
    in the response body
  - Assert justification field is null or absent in response

test_justification_not_in_logs:
  - Capture log output during POST /override
  - Assert justification text does not appear in any log line

---

## Rules
- Do NOT modify any node, router (beyond conftest fixtures),
  model, schema, or frontend files.
- Do NOT make real LLM API calls — use mock_report_assembler
  fixture for all tests that run the graph past HITL.
- DO use a real test database for all persistence tests.
- Every test must be fully isolated — unique run_id,
  unique tender, clean DB state per test.
- Use pytest.mark.asyncio for all async tests.
- For tests that poll for state="complete": max 15 second
  timeout (report_assembler stub is fast but checkpoint
  operations take time). Fail with explicit message if exceeded.
- test_double_approve_race_condition is the most important
  test in this suite — it validates the "resuming"
  intermediate state prevents double-resume.

---

## When you finish
Show me:
1. Total test functions created
2. Run the full suite:
   pytest tests/test_hitl.py -v
   Show actual terminal output
3. Confirm test_double_approve_race_condition passes —
   show me its specific output line and the asyncio.gather
   pattern used
4. Confirm test_hitl_overrides_row_preserved_on_resume_failure
   passes — show me the patch pattern used to simulate
   the failure
5. Confirm AC coverage — map every Acceptance Criteria
   item from REQ-007 to at least one test:
   "AC1 → test_approve_transitions_run_to_complete ✓"

REQ-007 is only complete once all 4 slices pass review.
Do not start REQ-008 until I explicitly tell you to.