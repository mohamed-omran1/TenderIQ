Read the following documents before writing any code:
- docs/reqs/REQ-004_Risk_Radar_Node.md
- docs/02_Architecture.md (section 3.2 — Fan-out/Fan-in Pattern,
  section 4 — Cost Tracking Middleware)

You are implementing **REQ-004 — Slice 2 (Node Logic) only**.

Slice 1 is already complete. The following is available:
- app/agents/skills/risk_clause_extraction.py → RiskFinding,
  RiskRadarOutput Pydantic schemas, SEVERITY_RUBRIC, FIDIC taxonomy,
  RISK_RADAR_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES

REQ-003 is already complete. The following is available:
- app/agents/state.py → TenderState (DO NOT MODIFY — read only)
- app/agents/graph.py → compiled graph with risk_radar wired
  as a stub node (DO NOT MODIFY graph.py itself)
- app/middleware/cost_tracker.py → CostTrackingHandler

The current stub at app/agents/nodes/risk_radar.py looks like this
(you are REPLACING this function body, not the file's existence):

  async def risk_radar_node(state: TenderState, config: RunnableConfig) -> dict:
      logger.info(f"[STUB] risk_radar executed for run {state['run_id']}")
      return {"risk_findings": [{"category": "stub", "severity": "low",
        "clause_text": "STUB", "explanation": "Stub — REQ-004 pending"}]}

---

## Your scope (do not touch anything outside this list)
- app/agents/nodes/risk_radar.py (replace stub logic only)
- app/agents/retrieval.py (create — anchor-query retrieval helper,
  if it does not already exist from REQ-001)

---

## What to implement

### 1. Anchor-query retrieval (app/agents/retrieval.py if new,
   or add a function here if a retrieval helper already exists)

  RISK_ANCHOR_QUERIES = [
      "penalty for delay in completion",
      "performance bond and letter of guarantee requirements",
      "termination for default or convenience",
      "liquidated damages and liability caps",
      "FIDIC sub-clause conditions",
  ]

  async def retrieve_risk_relevant_chunks(
      tender_id: str,
      chunks: list[dict],
      top_k_per_query: int = 5,
  ) -> list[dict]:
      """
      For each anchor query, run a pgvector similarity search against
      this tender's chunks and return the union of top_k_per_query
      results per query, deduplicated by chunk_index.
      """

  Use the existing embedding model and pgvector query pattern
  established in REQ-001's Ingestor — do not introduce a different
  embedding model or distance metric.

### 2. Real risk_radar_node implementation

  async def risk_radar_node(state: TenderState, config: RunnableConfig) -> dict:

  Logic:
    a) Call retrieve_risk_relevant_chunks(state["tender_id"], state["chunks"])
    b) If the returned list is empty: return {"risk_findings": []}
       immediately — this is a valid outcome per REQ-004's
       Alternative Flows, not an error.
    c) Build the LLM call using:
       - RISK_RADAR_SYSTEM_PROMPT from the skill package
       - FEW_SHOT_EXAMPLES from the skill package (formatted as
         prior messages or embedded in the prompt, your choice
         based on what the LLM provider's structured-output API
         supports best)
       - The retrieved chunks as the user content
       - RiskRadarOutput as the structured output schema (use the
         provider's native structured output / tool-calling mode,
         not prompt-engineered JSON parsing)
    d) Attach a CostTrackingHandler instance with node_name="risk_radar"
       to this LLM call's callbacks.
    e) On schema validation failure: retry the call once with the
       same prompt. On second failure: log the raw malformed response
       at WARNING level (without logging clause_text content from
       OTHER successful calls) and return {"risk_findings": []}.
    f) On LLM API failure (network/rate-limit): retry with exponential
       backoff, 3 attempts, consistent with REQ-001's embedding retry
       pattern. On exhausted retries, raise the exception so it
       propagates to the graph-level failure handling from REQ-003.
    g) Deduplicate findings: if two findings have semantically similar
       clause_text (use a simple embedding-similarity check, threshold
       0.92, consistent with the Architecture's semantic dedup pattern)
       and the same category, keep only one — prefer the finding with
       English clause_text if one of the duplicates is in Arabic and
       the other in English.
    h) Return {"risk_findings": [f.model_dump() for f in deduplicated_findings]}

  This function signature and return shape must exactly match what
  REQ-003's aggregator node already expects — do not change the
  key name "risk_findings" or its list-of-dict structure.

---

## Dependency versions to use
Use Context7 to confirm the exact current API for:
- The LLM provider's structured output / tool-calling syntax
  (e.g. with_structured_output() method signature if using
  LangChain's chat model wrapper)
- pgvector similarity search syntax consistent with whatever
  REQ-001 already established — do not introduce a new pattern,
  match the existing one exactly.

---

## Rules
- Do NOT modify app/agents/graph.py or app/agents/state.py.
- Do NOT modify app/agents/skills/risk_clause_extraction.py.
- Do NOT create any frontend or test files.
- Do NOT change the TenderState field names or structure.
- clause_text and explanation content must never appear in log
  statements — only generic metadata (run_id, node_name, finding
  count, category) may be logged.
- The retrieval step must use top_k_per_query and deduplicate by
  chunk_index BEFORE sending to the LLM — do not send every chunk
  in the tender to the LLM regardless of relevance.
- If FEW_SHOT_EXAMPLES from the skill package contains Arabic
  content you flagged earlier as unverified, use it as-is for now
  but add a comment: "# TODO: verify Arabic legal phrasing accuracy"

---

## When you finish
Show me:
1. Full file tree of everything you created or modified
2. Confirm the function signature and return shape exactly match
   what the REQ-003 aggregator expects — show me the aggregator's
   code that reads state["risk_findings"] alongside this node's
   return statement
3. Run a manual test against ONE real or sample chunk set
   (not the full graph) and show me the actual RiskFinding objects
   returned — I want to see real output, not just "it works"
4. Confirm retry logic exists for both schema-validation failures
   (max 1 retry) and LLM API failures (max 3 retries, exponential
   backoff) — show me both retry blocks
5. Confirm clause_text/explanation never appear in any log statement
   — grep your own code for logger calls and show me each one

Do not move to Slice 3 until I explicitly tell you to.