Read the following documents before writing any code:
- docs/reqs/REQ-006_Financial_Analyst_Node.md

You are implementing **REQ-006 — Slice 5 (QA) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- app/agents/skills/financial_extraction.py → skill package
- app/agents/nodes/financial_analyst.py → real extraction node
- financial_commitments table → persisted atomically
- GET /tenders/{id}/financial → returns ordered commitments
- FinancialSummaryCard frontend component

---

## Your scope (do not touch anything outside this list)
- tests/test_financial_analyst.py (create)
- tests/conftest.py (add fixtures only if not already present —
  do not remove any existing fixtures from REQ-001 through REQ-005)

---

## What to implement

A pytest test suite using pytest-asyncio and a real test database
(TEST_DATABASE_URL). Mock the LLM — no real API calls.
Consistent with REQ-004 and REQ-005 QA approach.

### Fixtures needed (add to conftest.py if not present)
- mock_financial_llm: returns a valid FinancialOutput with:
    contract_value: SAR 35,000,000
    bonds: [performance bond 10%, advance payment guarantee 15%]
    liquidated_damages: SAR 5,000/day, cap SAR 3,500,000 (10%)
    payment_schedule: 3 milestones (20% on signing,
      50% on completion, 30% on taking-over certificate)
    retention_rate: 5.0
    advance_payment: SAR 5,250,000

- mock_financial_llm_invalid_currency: returns a FinancialOutput
    where contract_value.currency = "Riyals" (not ISO 4217)
    and one bond amount.currency = "INVALID_CURR"

- mock_financial_llm_malformed: returns a string that fails
    FinancialOutput schema validation

- mock_financial_llm_api_error: raises APIConnectionError
    on every call

- mock_financial_llm_bilingual_duplicate: returns a
    FinancialOutput where bonds contains TWO entries for
    the same performance bond (one from Arabic chunk,
    one from English chunk — simulating pre-dedup state)
    Note: deduplication should happen in the node, so this
    fixture tests that the node handles it correctly.

- sample_financial_chunks: 6 chunk dicts covering bond
    requirements, payment terms, LD clauses, and contract value

### Test cases — implement ALL of the following

# --- Schema and output contract ---

test_return_key_matches_aggregator_contract:
  - Call financial_analyst_node with mock_financial_llm
  - Assert return dict has EXACTLY one key: "financial_summary"
  - Assert no extra keys

test_financial_summary_shape_is_consistent:
  - Call financial_analyst_node
  - Assert financial_summary always has these keys regardless
    of content: contract_value, bonds, liquidated_damages,
    payment_schedule, retention_rate, advance_payment
  - Assert bonds and payment_schedule are always lists (never null)
  - Assert contract_value, liquidated_damages, advance_payment
    are dict or null (never missing key entirely)

test_summary_never_contains_stub_values:
  - Call financial_analyst_node with mock_financial_llm
  - Assert "stub" key not in financial_summary
  - Assert financial_summary != {"stub": True, "bonds": [],
    "commitments": []}  (the old REQ-003 stub shape)

# --- Currency validation ---

test_valid_iso_currency_passes_unchanged:
  - Call validate_and_normalise_currency("SAR")
  - Assert return == ("SAR", False)

test_currency_normalisation_map_applied:
  - Call validate_and_normalise_currency("Riyals")
  - Assert return == ("SAR", False)
  - Call validate_and_normalise_currency("ريال")
  - Assert return == ("SAR", False)
  - Call validate_and_normalise_currency("Dirhams")
  - Assert return == ("AED", False)

test_unknown_currency_flagged:
  - Call validate_and_normalise_currency("INVALID_CURR")
  - Assert return == ("UNKNOWN", True)
  - Call validate_and_normalise_currency("")
  - Assert return == ("UNKNOWN", True)

test_all_6_gcc_currencies_normalise_correctly:
  - Test at least one variant for each GCC currency:
    SAR, AED, QAR, KWD, BHD, OMR — both English and Arabic
  - Assert all return (correct_iso_code, False)

test_invalid_currency_in_llm_output_normalised_in_postprocess:
  - Use mock_financial_llm_invalid_currency
  - Call financial_analyst_node
  - Assert financial_summary["contract_value"]["currency"] == "SAR"
    (normalised from "Riyals")
  - Assert financial_summary["bonds"][1]["amount"]["currency"] == "UNKNOWN"
    (invalid code → UNKNOWN)
  - Assert financial_summary["bonds"][1]["amount"]["needs_review"] == True

test_unknown_currency_sets_needs_review_true:
  - Use mock_financial_llm_invalid_currency
  - Call financial_analyst_node
  - Assert at least one commitment in the output has
    needs_review=True

# --- Deduplication ---

test_bilingual_duplicate_bond_produces_one_entry:
  - Use mock_financial_llm_bilingual_duplicate (two bonds,
    same type, similar conditions — one Arabic, one English)
  - Call financial_analyst_node
  - Assert len(financial_summary["bonds"]) == 1 (not 2)

# --- Error handling ---

test_malformed_output_retries_once_then_degrades:
  - Use mock_financial_llm_malformed
  - Assert LLM called exactly 2 times
  - Assert return contains "error" key in financial_summary
  - Assert financial_summary["bonds"] == []
  - Assert financial_summary["payment_schedule"] is None or []
  - Assert no exception propagates

test_api_failure_retries_three_times_then_raises:
  - Use mock_financial_llm_api_error
  - Assert LLM called exactly 3 times
  - Assert exception propagates (pytest.raises)

test_error_path_financial_summary_has_required_keys:
  - Use mock_financial_llm_malformed (triggers degraded path)
  - Assert financial_summary always contains:
    "error", "bonds", "commitments", "payment_schedule"
  - Even on failure, the shape must be consistent so
    the Aggregator never crashes on missing keys

# --- Retrieval ---

test_financial_retrieval_uses_different_queries_from_risk_and_scope:
  - Import all three anchor query lists:
    RISK_ANCHOR_QUERIES, SCOPE_ANCHOR_QUERIES,
    FINANCIAL_ANCHOR_QUERIES
  - Assert no query string appears in more than one list
  - (All three retrievers are intentionally distinct)

test_empty_financial_retrieval_falls_back_to_first_15_chunks:
  - Patch retrieve_financial_chunks to return []
  - Provide state with 20 sample chunks
  - Call financial_analyst_node
  - Assert LLM was called with content from first 15 chunks
    (verify via mock_financial_llm call args)

# --- Flatten helper ---

test_flatten_produces_correct_row_count:
  - Call _flatten_financial_summary() with mock output dict
    containing: 1 contract_value, 2 bonds, 1 LD,
    3 milestones, retention, advance_payment
  - Assert len(result) == 9 (one row per item)

test_flatten_skips_null_items:
  - Call _flatten_financial_summary() with summary where
    contract_value=None, liquidated_damages=None,
    retention_rate=None, advance_payment=None
  - Assert result only contains rows for bonds and milestones
  - Assert no row has commitment_type="contract_value"
    or "liquidated_damages" or "retention" or "advance_payment"

test_flatten_skips_error_summary:
  - Call _flatten_financial_summary() with:
    {"error": "malformed", "bonds": [], "payment_schedule": None}
  - Assert return == []
  - Assert no exception raised

test_flatten_includes_run_id_in_every_row:
  - Call _flatten_financial_summary() with valid summary
    and a specific run_id UUID
  - Assert every dict in the result has
    run_id == the provided UUID

# --- Security ---

test_financial_values_never_appear_in_logs:
  - Capture all log output during financial_analyst_node call
  - Assert amount_value (e.g. 35000000.0) does not appear
    in any log line as a string
  - Assert amount_currency values do not appear in logs
  - Only metadata (run_id, counts) may be logged

test_currency_warning_log_has_no_value:
  - Use mock_financial_llm_invalid_currency
    (triggers currency normalisation warning)
  - Capture log output
  - Assert WARNING log exists for the invalid currency
  - Assert the log line does NOT contain the actual
    amount_value number

# --- Cost tracking ---

test_cost_tracker_fires_with_correct_node_name:
  - Run financial_analyst_node with mock_financial_llm
  - Assert one llm_cost_events row created with
    node_name="financial_analyst" and correct run_id

test_cost_tracker_fires_on_retry_attempts:
  - Use mock_financial_llm_malformed (2 LLM calls)
  - Assert exactly 2 llm_cost_events rows with
    node_name="financial_analyst"

# --- Persistence (integration) ---

test_financial_commitments_persisted_on_awaiting_hitl:
  - Run full pipeline to awaiting_hitl (mock LLM)
  - Query DB:
    SELECT commitment_type, amount_currency, needs_review
    FROM financial_commitments WHERE run_id = '<run_id>'
    ORDER BY commitment_type
  - Assert rows exist for each commitment type present
    in the mock output
  - Assert analysis_runs.state = "awaiting_hitl"

test_error_path_produces_no_financial_commitments_rows:
  - Run full pipeline where financial_analyst returns
    error path (use mock_financial_llm_malformed)
  - Assert zero financial_commitments rows for this run_id
    (error path skips INSERT per Slice 3 rules)
  - Assert analysis_runs.state = "awaiting_hitl" still set
    (other operations in atomic block must still succeed)

test_all_four_operations_atomic_commit:
  - This is the master atomicity test for the combined
    REQ-004 + REQ-005 + REQ-006 commit block
  - Simulate DB failure AFTER risk_findings INSERT but
    BEFORE the final commit
  - Assert NONE of the following persist:
    * risk_findings rows for this run_id
    * analysis_runs.feasibility_score updated
    * financial_commitments rows for this run_id
    * analysis_runs.state = "awaiting_hitl"
  - All four must roll back together

test_get_financial_endpoint_returns_correct_types:
  - Run pipeline to awaiting_hitl
  - Call GET /tenders/{id}/financial
  - Assert response is a list
  - Assert each item has all required fields:
    id, commitment_type, amount_value, amount_currency,
    percentage, description, needs_review, source_chunk_index
  - Assert commitment_type values are from the valid enum:
    bond | liquidated_damages | payment_milestone |
    retention | advance_payment | contract_value

test_get_financial_returns_404_before_awaiting_hitl:
  - Create a run in "running" state (not yet awaiting_hitl)
  - Call GET /tenders/{id}/financial
  - Assert HTTP 404

# --- Boundary values ---

test_all_needs_review_false_when_all_currencies_valid:
  - Use mock_financial_llm (all SAR — valid ISO code)
  - Call financial_analyst_node
  - Assert every MonetaryValue in financial_summary has
    needs_review=False

test_needs_review_summary_count_matches_flagged_items:
  - Use mock_financial_llm_invalid_currency
    (1 UNKNOWN currency → 1 needs_review=True item)
  - Count items with needs_review=True in financial_summary
  - Assert count == 1

---

## Rules
- Do NOT modify any node, router, model, schema, or frontend files.
- Do NOT make real LLM API calls — mock the LLM for all tests.
- DO use a real test database for all persistence tests.
- Every test must be fully isolated — clean DB state per test,
  unique run_id per test.
- Use pytest.mark.asyncio for all async tests.
- For pipeline tests that wait for awaiting_hitl: max 10s
  timeout with explicit failure message.
- test_all_four_operations_atomic_commit is the most critical
  test in this suite — it validates all three REQs' persistence
  as a single unit. Flag it clearly in the output.

---

## When you finish
Show me:
1. Total test functions created
2. Run the full suite:
   pytest tests/test_financial_analyst.py -v
   Show actual terminal output.
3. Confirm test_all_four_operations_atomic_commit passes —
   show me its specific output line
4. Confirm all 6 GCC currency tests pass —
   show me test_all_6_gcc_currencies_normalise_correctly output
5. Confirm AC coverage — map every Acceptance Criteria
   item from REQ-006 to at least one test:
   "AC1 → test_return_key_matches_aggregator_contract ✓"

REQ-006 is only complete once all 5 slices pass review.
Do not start REQ-007 until I explicitly tell you to.