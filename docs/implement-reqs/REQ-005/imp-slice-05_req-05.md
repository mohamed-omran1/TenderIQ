Read the following documents before writing any code:
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md

You are implementing **REQ-005 — Slice 5 (QA) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- app/agents/skills/feasibility_scoring.py → skill package
- app/agents/nodes/feasibility_scorer.py → real LLM scoring node
- analysis_runs.feasibility_score persisted atomically on awaiting_hitl
- GET /tenders/{id}/status → includes feasibility_score
- FeasibilityScoreCard frontend component

---

## Your scope (do not touch anything outside this list)
- tests/test_feasibility_scorer.py (create)
- tests/conftest.py (add fixtures if not already present —
  do not remove existing fixtures from REQ-001/002/003/004)

---

## What to implement

A pytest test suite using pytest-asyncio and a real test database
(TEST_DATABASE_URL). Mock the LLM client — do NOT make real LLM
API calls. Consistent with REQ-004 QA approach.

### Fixtures needed (add to conftest.py if not present)
- mock_feasibility_llm: returns a valid FeasibilityOutput with
  all 5 dimensions scored (use varied scores to test all
  colour thresholds — include at least one score > 20 and
  one score < 0 to test clamping)
- mock_feasibility_llm_malformed: returns a string that fails
  FeasibilityOutput schema validation
- mock_feasibility_llm_api_error: raises APIConnectionError
  on every call
- company_profile_fixture: a company with a fully populated
  profile (all 6 fields), inserted directly into the test DB
- sample_scope_chunks: 5 chunk dicts covering project scope,
  value, timeline, location, and qualifications

### Test cases — implement ALL of the following

# --- Schema and output contract ---

test_return_keys_match_aggregator_contract:
  - Call feasibility_scorer_node with mock_feasibility_llm
  - Assert return dict has EXACTLY these keys:
    "feasibility_score", "feasibility_breakdown"
  - Assert no extra keys (strict schema)

test_feasibility_breakdown_has_all_5_dimensions:
  - Call feasibility_scorer_node
  - Assert feasibility_breakdown has exactly these keys:
    technical_fit, financial_capacity, timeline,
    geographic_scope, past_experience
  - Assert each value has "score" (int) and "rationale" (str)

test_composite_score_equals_sum_of_dimensions:
  - Mock LLM to return specific scores:
    technical_fit=18, financial_capacity=14,
    timeline=16, geographic_scope=20, past_experience=12
    (sum = 80)
  - Assert feasibility_score == 80.0 exactly
  - This verifies the Python-side sum, not the LLM

test_composite_score_is_always_float:
  - Assert type(result["feasibility_score"]) is float
  - Not int, not None

# --- Clamping ---

test_out_of_range_high_score_is_clamped:
  - Mock LLM to return technical_fit score = 25 (above max 20)
  - Assert feasibility_breakdown["technical_fit"]["score"] == 20
  - Assert feasibility_score reflects clamped value (not 25)

test_out_of_range_low_score_is_clamped:
  - Mock LLM to return geographic_scope score = -3 (below 0)
  - Assert feasibility_breakdown["geographic_scope"]["score"] == 0
  - Assert feasibility_score reflects clamped value (not -3)

test_clamping_logs_warning_with_run_id:
  - Mock LLM to return an out-of-range score
  - Capture log output
  - Assert a WARNING log line contains the run_id and
    the dimension name that was clamped
  - Assert no profile data appears in the warning log

# --- Error handling ---

test_malformed_output_retries_once_then_degrades:
  - Use mock_feasibility_llm_malformed
  - Assert LLM called exactly 2 times (initial + 1 retry)
  - Assert return is:
    {
      "feasibility_score": 0.0,
      "feasibility_breakdown": {
        "error": "Scoring unavailable — malformed LLM response"
      }
    }
  - Assert no exception propagates

test_api_failure_retries_three_times_then_raises:
  - Use mock_feasibility_llm_api_error
  - Assert LLM called exactly 3 times
  - Assert exception propagates (pytest.raises)

test_retry_strategies_are_independent:
  - Run both tests above and confirm call counts are correct:
    schema failure: exactly 2 calls
    API failure:    exactly 3 calls
  - These must be separate code paths — verify by inspecting
    the call count on each mock independently

# --- Retrieval ---

test_scope_retrieval_is_separate_from_risk_retrieval:
  - Import both retrieve_risk_relevant_chunks and
    retrieve_scope_relevant_chunks from agents/retrieval.py
  - Assert they use different anchor query lists:
    risk uses RISK_ANCHOR_QUERIES from risk_clause_extraction.py
    scope uses SCOPE_ANCHOR_QUERIES from feasibility_scoring.py
  - Assert the two query lists have zero overlap

test_empty_scope_retrieval_falls_back_to_first_20_chunks:
  - Patch retrieve_scope_relevant_chunks to return []
  - Provide state with 25 sample chunks
  - Call feasibility_scorer_node
  - Assert the LLM was called with content from the first 20
    chunks (by chunk_index), not an empty context

test_scope_chunks_have_no_duplicate_chunk_index:
  - Call retrieve_scope_relevant_chunks() directly
    with sample_scope_chunks
  - Assert all returned chunk_index values are unique

# --- Profile data security ---

test_profile_data_never_appears_in_logs:
  - Capture all log output during feasibility_scorer_node call
  - Assert none of these strings appear in any log line:
    * Any value from financial_capacity fields
      (annual_turnover, available_bonding_capacity)
    * Any value from past_projects entries
  - Assert "financial_capacity" key itself does not appear in logs

test_profile_lookup_called_with_correct_company_id:
  - Patch profile_lookup.ainvoke to record its call args
  - Call feasibility_scorer_node with state["company_id"] = "test-uuid"
  - Assert profile_lookup.ainvoke was called with
    {"company_id": "test-uuid"} exactly once

# --- Cost tracking ---

test_cost_tracker_fires_with_correct_node_name:
  - Run feasibility_scorer_node with mock_feasibility_llm
  - Assert one llm_cost_events row was created with
    node_name="feasibility_scorer" and correct run_id

test_cost_tracker_does_not_fire_on_degraded_path:
  - Run feasibility_scorer_node with mock_feasibility_llm_malformed
    (which degrades to 0.0 after 2 retries)
  - Note: the handler DOES fire on LLM calls, even failed ones
  - Assert llm_cost_events has exactly 2 rows (initial + retry)
    both with node_name="feasibility_scorer"

# --- Persistence (integration) ---

test_feasibility_score_persisted_on_awaiting_hitl:
  - Run full analysis pipeline to awaiting_hitl
    (mock LLM returning score=80.0)
  - Query DB directly:
    SELECT feasibility_score, state FROM analysis_runs
    WHERE id = '<run_id>'
  - Assert state = "awaiting_hitl"
  - Assert feasibility_score = 80.0 (not null, not 0.0)

test_all_three_operations_share_one_commit:
  - This is the atomicity test for the combined REQ-004 +
    REQ-005 commit block
  - Simulate a DB failure AFTER the risk_findings INSERT
    but BEFORE the UPDATE commits
  - Assert none of the following are present in the DB:
    * risk_findings rows for this run_id
    * analysis_runs.feasibility_score updated
    * analysis_runs.state = "awaiting_hitl"
  - All three must roll back together

test_feasibility_score_in_status_response:
  - Run analysis to awaiting_hitl
  - Call GET /tenders/{id}/status
  - Assert feasibility_score field is present in response
  - Assert value matches what was computed by the node

# --- Boundary values ---

test_maximum_possible_score:
  - Mock LLM to return all 5 dimensions at score=20
  - Assert feasibility_score == 100.0
  - Assert all dimension scores in breakdown are 20

test_minimum_possible_score:
  - Mock LLM to return all 5 dimensions at score=0
  - Assert feasibility_score == 0.0
  - Assert all dimension scores in breakdown are 0

test_score_is_never_outside_0_100_range:
  - Mock LLM to return all dimensions at score=25 (above max)
  - After clamping: each dimension becomes 20, sum = 100
  - Assert feasibility_score == 100.0, never 125.0

---

## Rules
- Do NOT modify any node, router, model, schema, or frontend files.
- Do NOT make real LLM API calls — mock the LLM for all tests.
- DO use a real test database for persistence tests.
- Every test must be fully isolated with its own run_id
  and clean DB state — no test depends on another.
- Use pytest.mark.asyncio for all async tests.
- For polling tests (waiting for awaiting_hitl), always use
  a max timeout of 10 seconds with explicit failure message
  if exceeded — consistent with REQ-003/004 QA approach.
- The atomicity test (test_all_three_operations_share_one_commit)
  is the most critical test in this suite — it verifies the
  combined REQ-004 + REQ-005 commit block as a single unit.

---

## When you finish
Show me:
1. Total test functions created
2. Run the full suite and show me the actual output:
   pytest tests/test_feasibility_scorer.py -v
3. Confirm the atomicity test passes — show me the specific
   output line for test_all_three_operations_share_one_commit
4. Confirm the two retry strategies have correct independent
   call counts — show me the output for:
   test_malformed_output_retries_once_then_degrades
   test_api_failure_retries_three_times_then_raises
5. Confirm AC coverage — map every Acceptance Criteria item
   from REQ-005 to at least one test:
   "AC1 → test_composite_score_equals_sum_of_dimensions ✓"

REQ-005 is only complete once all 5 slices pass review.
Do not start REQ-006 until I explicitly tell you to.