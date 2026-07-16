Read the following documents before writing any code:
- docs/reqs/REQ-006_Financial_Analyst_Node.md
- docs/02_Architecture.md (section 3.2 — Fan-out/Fan-in Pattern)

You are implementing **REQ-006 — Slice 2 (Node Logic) only**.

Slice 1 is already complete. The following is available:
- app/agents/skills/financial_extraction.py →
  MonetaryValue, BondRequirement, PaymentMilestone,
  LiquidatedDamages, FinancialOutput Pydantic schemas,
  FINANCIAL_ANCHOR_QUERIES, CURRENCY_NORMALISATION map,
  FINANCIAL_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES

REQ-003 is complete. The following is available:
- app/agents/state.py → TenderState (DO NOT MODIFY — read only)
- app/agents/graph.py → compiled graph (DO NOT MODIFY)
- app/middleware/cost_tracker.py → CostTrackingHandler
- app/agents/retrieval.py → retrieve_risk_relevant_chunks()
  and retrieve_scope_relevant_chunks() already exist —
  add retrieve_financial_chunks() as a NEW function only,
  do not modify the existing two functions

The current stub at app/agents/nodes/financial_analyst.py:
  async def financial_analyst_node(state, config) -> dict:
      logger.info(f"[STUB] financial_analyst for {state['run_id']}")
      return {
          "financial_summary": {
              "stub": True, "bonds": [], "commitments": []
          }
      }

---

## Your scope (do not touch anything outside this list)
- app/agents/nodes/financial_analyst.py (replace stub logic)
- app/agents/retrieval.py (add retrieve_financial_chunks() only)

---

## What to implement

### 1. Add to app/agents/retrieval.py

  async def retrieve_financial_chunks(
      tender_id: str,
      chunks: list[dict],
      top_k_per_query: int = 4,
  ) -> list[dict]:
      """
      For each query in FINANCIAL_ANCHOR_QUERIES (imported from
      app/agents/skills/financial_extraction.py), run a pgvector
      similarity search against this tender's chunks and return
      the union of top_k_per_query results per query,
      deduplicated by chunk_index.
      """

  Must:
  - Use the SAME embedding model and pgvector pattern as
    retrieve_risk_relevant_chunks() and
    retrieve_scope_relevant_chunks() — no new pattern
  - Use FINANCIAL_ANCHOR_QUERIES from financial_extraction.py
    (not from risk or scope skill packages)
  - Return list[dict] with same shape: {content,
    detected_language, chunk_index}
  - Never return duplicate chunk_index values

  Fallback (per REQ-006 Alternative Flows):
  - If returned list is empty, return first 15 chunks
    ordered by chunk_index — not an empty list

### 2. Currency validation helper — in financial_analyst.py

  def validate_and_normalise_currency(currency_str: str) -> tuple[str, bool]:
      """
      Returns (normalised_iso_code, needs_review).
      - If currency_str is already a valid ISO 4217 code: return (currency_str, False)
      - If currency_str is in CURRENCY_NORMALISATION map: return (mapped_code, False)
      - Otherwise: return ("UNKNOWN", True)
      Never raises — always returns a valid tuple.
      """

  Valid ISO 4217 codes to accept without normalisation
  (at minimum): SAR, AED, EGP, USD, QAR, KWD, BHD, OMR,
  EUR, GBP. Any code not in this list and not in
  CURRENCY_NORMALISATION triggers the UNKNOWN fallback.

### 3. Post-processing helper — in financial_analyst.py

  def postprocess_financial_output(
      output: FinancialOutput,
      run_id: str,
  ) -> dict:
      """
      Validates and normalises the LLM output before writing
      to TenderState. Returns the financial_summary dict.
      """

  Steps:
  a) For every MonetaryValue in the output (contract_value,
     all bond amounts, LD rate and cap, advance_payment):
     apply validate_and_normalise_currency() and update
     the value in place.

  b) For any MonetaryValue where needs_review becomes True,
     log a single WARNING:
       f"run_id={run_id} currency normalisation required "
       f"for {field_name} — set to UNKNOWN"
     Do NOT log the actual value.

  c) For bonds expressed as percentage only
     (amount.value == 0.0 and percentage is not None):
     if contract_value is known and not needs_review:
       compute amount.value = (percentage / 100) * contract_value.value
       set amount.currency = contract_value.currency
       set amount.needs_review = False
     else:
       leave amount.value = 0.0 and set amount.needs_review = True

  d) Return output.model_dump() as the financial_summary dict.

### 4. Real financial_analyst_node implementation

  async def financial_analyst_node(
      state: TenderState,
      config: RunnableConfig
  ) -> dict:

  Logic:
    a) Retrieve finance-relevant chunks:
       finance_chunks = await retrieve_financial_chunks(
           state["tender_id"], state["chunks"]
       )
       # Fallback to first 15 chunks handled inside retrieval fn

    b) Build LLM call:
       - System: FINANCIAL_SYSTEM_PROMPT
       - FEW_SHOT_EXAMPLES as prior messages
       - User content: formatted string of retrieved chunk
         contents with their chunk_index labels
       - FinancialOutput as structured output schema
         (with_structured_output — confirm syntax via Context7)
       - CostTrackingHandler(
             run_id=state["run_id"],
             node_name="financial_analyst",
             db=<session>
         ) attached to callbacks

    c) On schema validation failure: retry once.
       On second failure:
         return {
             "financial_summary": {
                 "error": "Financial extraction unavailable "
                          "— malformed LLM response",
                 "bonds": [],
                 "commitments": [],
                 "payment_schedule": None,
             }
         }
       Do NOT raise.

    d) On LLM API failure: retry with exponential backoff,
       3 attempts. On exhausted retries: raise.
       (Same pattern as REQ-004 and REQ-005 — consistent.)

    e) On successful LLM output:
       summary = postprocess_financial_output(output, state["run_id"])

    f) Log metadata only (never log values):
       logger.info(
           f"run_id={state['run_id']} financial_analyst complete — "
           f"bonds={len(summary.get('bonds', []))} "
           f"milestones={len(summary.get('payment_schedule', []))} "
           f"has_ld={summary.get('liquidated_damages') is not None}"
       )

    g) Return {"financial_summary": summary}

---

## Dependency versions to use
Use Context7 to confirm:
- with_structured_output() current signature for nested
  Pydantic schemas (FinancialOutput has nested models —
  confirm the provider supports nested structured output
  vs. requiring a flat schema)
- retrieve_financial_chunks() pgvector query pattern —
  must be identical to the pattern already used in
  retrieve_risk_relevant_chunks() and
  retrieve_scope_relevant_chunks()

---

## Rules
- Do NOT modify agents/graph.py or agents/state.py.
- Do NOT modify agents/skills/financial_extraction.py.
- Do NOT modify retrieve_risk_relevant_chunks() or
  retrieve_scope_relevant_chunks() in retrieval.py —
  add retrieve_financial_chunks() as a new function only.
- Do NOT create any frontend or test files.
- Do NOT change the return dict key "financial_summary" —
  this is what REQ-003's aggregator expects.
- Financial values (amount_value, currency, contract amounts)
  must NEVER appear in any log statement — only metadata
  (run_id, node_name, count of extracted items).
- The three retrieval functions must all use different
  anchor query lists:
    retrieve_risk_relevant_chunks → RISK_ANCHOR_QUERIES
    retrieve_scope_relevant_chunks → SCOPE_ANCHOR_QUERIES
    retrieve_financial_chunks → FINANCIAL_ANCHOR_QUERIES
  Never mix query lists between retrievers.
- validate_and_normalise_currency() must be a pure function
  (no I/O, no DB, no logging) — independently unit-testable.
- postprocess_financial_output() must never raise —
  wrap in try/except and return the raw model_dump() if
  post-processing itself fails unexpectedly.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (2 files)
2. Confirm the three retrieval functions use different anchor
   query lists — show me the import line in each function:
   retrieve_risk_relevant_chunks  → imports RISK_ANCHOR_QUERIES from ...
   retrieve_scope_relevant_chunks → imports SCOPE_ANCHOR_QUERIES from ...
   retrieve_financial_chunks      → imports FINANCIAL_ANCHOR_QUERIES from ...
3. Run validate_and_normalise_currency() directly and show
   me the output for these inputs:
   python -c "
   from app.agents.nodes.financial_analyst import (
       validate_and_normalise_currency
   )
   cases = [
       'SAR',        # valid ISO — expect ('SAR', False)
       'Riyals',     # in NORMALISATION map — expect ('SAR', False)
       'ريال',       # Arabic — expect ('SAR', False)
       'INVALID',    # unknown — expect ('UNKNOWN', True)
       'Dollars',    # map entry — expect ('USD', False)
   ]
   for c in cases:
       print(c, '->', validate_and_normalise_currency(c))
   "
4. Run a manual test with sample chunks and show me the
   ACTUAL financial_summary output — I want to see real
   extracted values with currencies, not just "it works":
   python -c "
   import asyncio
   from app.agents.nodes.financial_analyst import financial_analyst_node
   # ... setup state with real chunks
   result = asyncio.run(financial_analyst_node(state, {}))
   import json
   print(json.dumps(result['financial_summary'], indent=2))
   "
5. Confirm retrieve_financial_chunks() is a new function
   and the existing two retrievers are unchanged:
   git diff app/agents/retrieval.py | grep '^+' | head -30

Do not move to Slice 3 until I explicitly tell you to.