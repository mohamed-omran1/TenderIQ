Read the following documents before writing any code:
- docs/reqs/REQ-004_Risk_Radar_Node.md

You are implementing **REQ-004 — Slice 5 (QA + Eval) only**.

Slices 1, 2, 3, and 4 are already complete and working:
- app/agents/skills/risk_clause_extraction.py → skill package
- app/agents/nodes/risk_radar.py → real LLM extraction node
- risk_findings table → persisted when run reaches awaiting_hitl
- GET /tenders/{id}/findings → returns ordered findings
- RiskRadarTable frontend component

---

## Your scope (do not touch anything outside this list)
- tests/test_risk_radar.py (create)
- eval/labelled_sample_tender.json (create — ground truth file)
- eval/run_eval.py (create — manual eval script)

---

## What to implement

### PART A — Unit & Integration Tests (tests/test_risk_radar.py)

Use pytest + pytest-asyncio. Mock the LLM client — do NOT make
real LLM API calls in the test suite. Use a real test database
(TEST_DATABASE_URL) consistent with REQ-002/003 QA approach.

#### Fixtures needed (add to conftest.py if not present)
- mock_llm: a fixture that returns a fake ChatOpenAI (or equivalent)
  client whose ainvoke() returns a pre-defined RiskRadarOutput object
- mock_llm_malformed: a fixture whose ainvoke() returns a string
  that fails RiskRadarOutput schema validation
- mock_llm_api_error: a fixture whose ainvoke() raises an
  openai.APIConnectionError (or equivalent) on every call
- sample_chunks: a fixture returning 5 realistic chunk dicts
  matching the shape the Ingestor produces:
  [{"content": "...", "detected_language": "en", "chunk_index": 0}, ...]
  Include at least one chunk with Arabic content.

#### Test cases — implement ALL of the following

# --- Schema and output contract ---

test_risk_findings_schema_matches_aggregator_contract:
  - Call risk_radar_node with mock_llm and sample_chunks
  - Assert return is a dict with key "risk_findings"
  - Assert every item in risk_findings has exactly these keys:
    category, severity, clause_text, explanation,
    source_chunk_index, confidence
  - Assert no extra keys exist (strict schema check)

test_severity_values_are_always_from_enum:
  - Call risk_radar_node with mock_llm returning findings
    with all 4 severity values
  - Assert each severity is one of: critical | high | medium | low
  - Assert no free-text severity values are present

test_category_values_are_always_from_enum:
  - Same pattern for category:
    fidic | penalty | lg_bond | termination | other

# --- Retrieval ---

test_anchor_retrieval_returns_deduplicated_chunks:
  - Call retrieve_risk_relevant_chunks() directly with
    sample_chunks (bypass the full node)
  - Assert the return is a list[dict] with no duplicate chunk_index
    values — even if the same chunk scores high for multiple
    anchor queries, it must appear only once

test_empty_chunks_returns_empty_findings:
  - Call risk_radar_node with state["chunks"] = []
  - Assert return is {"risk_findings": []}
  - Assert no LLM call was made (mock_llm.ainvoke.call_count == 0)

test_no_relevant_chunks_returns_empty_findings:
  - Patch retrieve_risk_relevant_chunks to return []
  - Call risk_radar_node
  - Assert return is {"risk_findings": []}
  - Assert no LLM call was made

# --- Error handling ---

test_malformed_llm_response_retries_once_then_degrades:
  - Use mock_llm_malformed (always fails schema validation)
  - Call risk_radar_node
  - Assert LLM was called exactly 2 times (initial + 1 retry)
  - Assert return is {"risk_findings": []}
  - Assert no exception propagates to the caller

test_llm_api_failure_retries_three_times_then_raises:
  - Use mock_llm_api_error (always raises APIConnectionError)
  - Call risk_radar_node
  - Assert LLM was called exactly 3 times
  - Assert the exception DOES propagate (pytest.raises)
  - This is different from schema failure — API failure raises,
    schema failure degrades gracefully

test_retry_counts_are_independent:
  - Verify schema-validation retry (max 1) and API-error retry
    (max 3) are separate code paths — not a single shared retry
  - Run both test cases above and confirm call counts are correct

# --- Deduplication ---

test_duplicate_findings_are_deduplicated:
  - Patch retrieve_risk_relevant_chunks to return 3 chunks
    where 2 are semantically near-identical (same clause in
    slightly different wording)
  - Mock LLM to return a finding for each chunk (3 findings,
    2 near-duplicate)
  - Assert final risk_findings has 2 items, not 3

test_bilingual_duplicate_keeps_english_version:
  - Mock two findings with the same clause, one with
    clause_text in Arabic and one in English, same category
  - Assert the deduplicated output keeps the English version

# --- Cost tracking ---

test_cost_tracker_fires_on_successful_llm_call:
  - Run risk_radar_node with mock_llm that returns valid findings
  - Assert one llm_cost_events row was created with
    node_name="risk_radar" and the correct run_id

test_cost_tracker_does_not_fire_when_no_llm_called:
  - Run risk_radar_node with empty chunks (no LLM call)
  - Assert zero llm_cost_events rows were created for this run_id

# --- Security ---

test_clause_text_never_appears_in_logs:
  - Capture all log output during a risk_radar_node call
    that returns findings
  - Assert none of the clause_text values from the findings
    appear in any log line
  - Assert "clause_text" key itself does not appear in logs

# --- Persistence (integration) ---

test_findings_persisted_atomically_on_awaiting_hitl:
  - Run full analysis pipeline to awaiting_hitl
    (using mock_llm to avoid real LLM cost)
  - Assert risk_findings rows exist in DB for this run_id
  - Assert analysis_runs.state = "awaiting_hitl"
  - Simulate a crash BETWEEN the INSERT and UPDATE
    by wrapping the commit in a mock that fails on the second call
  - Assert neither the findings nor the state change persist
    (both roll back together)

test_get_findings_endpoint_returns_ordered_by_severity:
  - Insert risk_findings rows in random severity order directly
    to the test DB
  - Call GET /tenders/{id}/findings
  - Assert the response order is: critical → high → medium → low
  - Assert within the same severity, higher confidence comes first

---

### PART B — Manual Accuracy Evaluation

This is NOT a pytest test. It is a one-time measurement script
and a ground-truth file that become the baseline for REQ-012's
automated eval harness.

#### eval/labelled_sample_tender.json
Create this file manually (do NOT generate it with the LLM —
it must reflect your own reading of a real tender document):

  {
    "tender_name": "<name of the sample tender you used>",
    "source": "<where it came from — public tender, anonymised, etc.>",
    "total_chunks": <number>,
    "labelled_findings": [
      {
        "category": "penalty",
        "severity": "high",
        "clause_text": "<verbatim quote>",
        "explanation": "<your plain-English description>",
        "source_chunk_index": <number>
      },
      ...
    ]
  }

If you do not yet have a real tender document (this is the
Open Question from PRD §12), create a PLACEHOLDER file with
a clearly marked comment at the top:

  {
    "_note": "PLACEHOLDER — real labelled tender required before
    REQ-004 can be marked fully complete. See PRD Open Question #1.",
    "tender_name": null,
    "labelled_findings": []
  }

Do NOT fabricate a fake tender as if it were real. Either use
a real document or mark it as a placeholder explicitly.

#### eval/run_eval.py
A standalone script (not pytest) that:

  1. Loads eval/labelled_sample_tender.json
  2. If labelled_findings is empty (placeholder state), prints:
     "Eval skipped — no labelled tender available yet." and exits.
  3. Otherwise, runs the real risk_radar_node against the labelled
     tender's chunks (fetched from the DB by tender_id, passed as
     a CLI argument)
  4. Computes:
     - Recall: how many labelled findings were found by the node
       (match by clause_text substring overlap >= 0.7)
     - Precision: how many node findings match a labelled finding
     - F1 score
  5. Prints a structured report:
     "REQ-004 Accuracy Eval
      Tender: <tender_name>
      Labelled findings: <N>
      Model findings:    <N>
      Recall:    <X>% (target: >= 85%)
      Precision: <X>%
      F1:        <X>%
      Status: PASS / FAIL"
  6. Exits with code 0 if recall >= 85%, code 1 if below threshold.

Usage:
  python eval/run_eval.py --tender-id <uuid>

---

## Rules
- Do NOT modify any node, router, model, schema, or frontend files.
- Do NOT make real LLM API calls in the pytest suite —
  mock the LLM client for all unit/integration tests.
- DO use a real test database for persistence tests —
  consistent with REQ-002/003 QA approach.
- eval/run_eval.py IS allowed to make real LLM calls —
  it is a manual measurement tool, not an automated test.
- The labelled_findings in eval/labelled_sample_tender.json must be
  written by a human, not generated by the LLM. If you cannot
  produce this file from a real document, use the placeholder format.
- Every test must be fully isolated with its own run_id and
  clean DB state.

---

## When you finish
Show me:
1. Total test functions created in tests/test_risk_radar.py
2. Run the full suite and show me the actual output:
   pytest tests/test_risk_radar.py -v
3. Confirm the two retry strategies have independent call counts —
   show me the actual output from:
   test_malformed_llm_response_retries_once_then_degrades
   test_llm_api_failure_retries_three_times_then_raises
4. Show me eval/labelled_sample_tender.json —
   is it a real labelled tender or a placeholder?
   Either answer is acceptable — just be honest.
5. Run eval/run_eval.py (even if it exits early as placeholder)
   and show me the terminal output:
   python eval/run_eval.py --tender-id <any_valid_uuid>
6. Confirm AC coverage — map every Acceptance Criteria item
   from REQ-004 to at least one test or eval measurement:
   "AC1 → test_risk_findings_schema_matches_aggregator_contract ✓"

REQ-004 is only complete once all 5 slices pass review
AND eval/labelled_sample_tender.json exists (even as placeholder).
Do not start REQ-005 until I explicitly tell you to.