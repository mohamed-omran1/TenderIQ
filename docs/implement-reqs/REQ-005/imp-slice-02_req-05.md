Read the following documents before writing any code:
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md
- docs/02_Architecture.md(section 3.2 — Fan-out/Fan-in Pattern)

You are implementing **REQ-005 — Slice 2 (Node Logic) only**.

Slice 1 is already complete. The following is available:
- app/agents/skills/feasibility_scoring.py →
  DimensionScore, FeasibilityOutput Pydantic schemas,
  SCORING_DIMENSIONS rubric, SCOPE_ANCHOR_QUERIES,
  FEASIBILITY_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES

REQ-003 is complete. The following is available:
- app/agents/state.py → TenderState (DO NOT MODIFY — read only)
- app/agents/graph.py → compiled graph (DO NOT MODIFY)
- app/agents/tools/profile_lookup.py → async LangChain tool
- app/middleware/cost_tracker.py → CostTrackingHandler
- app/agents/retrieval.py → retrieve_risk_relevant_chunks()
  (from REQ-004 Slice 2 — reuse the retrieval pattern,
  do NOT copy the function, create a separate one for scope queries)

The current stub at app/agents/nodes/feasibility_scorer.py:
  async def feasibility_scorer_node(state, config) -> dict:
      logger.info(f"[STUB] feasibility_scorer for {state['run_id']}")
      return {
          "feasibility_score": 0.0,
          "feasibility_breakdown": {"stub": True}
      }

---

## Your scope (do not touch anything outside this list)
- app/agents/nodes/feasibility_scorer.py (replace stub logic)
- app/agents/retrieval.py (add retrieve_scope_relevant_chunks()
  function only — do not modify retrieve_risk_relevant_chunks())

---

## What to implement

### 1. Add to app/agents/retrieval.py

  async def retrieve_scope_relevant_chunks(
      tender_id: str,
      chunks: list[dict],
      top_k_per_query: int = 4,
  ) -> list[dict]:
      """
      For each query in SCOPE_ANCHOR_QUERIES (imported from
      app/agents/skills/feasibility_scoring.py), run a pgvector
      similarity search against this tender's chunks and return
      the union of top_k_per_query results per query,
      deduplicated by chunk_index.
      """

  This function must:
  - Use the SAME embedding model and pgvector query pattern
    as retrieve_risk_relevant_chunks() — do not introduce
    a different embedding model or distance metric
  - Use SCOPE_ANCHOR_QUERIES from feasibility_scoring.py
    (not RISK_ANCHOR_QUERIES from risk_clause_extraction.py)
  - Return list[dict] with same shape as input chunks:
    {content, detected_language, chunk_index}
  - Never return duplicate chunk_index values

  Fallback (per Alternative Flows in REQ-005):
  - If the returned list is empty, return the first 20 chunks
    ordered by chunk_index — not an empty list

### 2. Real feasibility_scorer_node implementation

  async def feasibility_scorer_node(
      state: TenderState,
      config: RunnableConfig
  ) -> dict:

  Logic:
    a) Fetch company profile:
       profile = await profile_lookup.ainvoke(
           {"company_id": state["company_id"]}
       )

    b) Retrieve scope-relevant chunks:
       scope_chunks = await retrieve_scope_relevant_chunks(
           state["tender_id"], state["chunks"]
       )
       # Fallback already handled inside retrieve_scope_relevant_chunks

    c) Build LLM input:
       - System: FEASIBILITY_SYSTEM_PROMPT
       - Include SCORING_DIMENSIONS rubric in the prompt context
       - Include FEW_SHOT_EXAMPLES as prior messages
       - User content: formatted string combining:
           * Company profile fields (all 6 fields from
             CompanyProfileSchema, clearly labelled)
           * Retrieved scope chunks (content + chunk_index)

    d) Call LLM with FeasibilityOutput as structured output schema.
       Use with_structured_output(FeasibilityOutput) — confirm
       exact syntax via Context7 before writing.
       Attach CostTrackingHandler(
           run_id=state["run_id"],
           node_name="feasibility_scorer",
           db=<session>
       ) to callbacks.

    e) On schema validation failure: retry once.
       On second failure:
         return {
             "feasibility_score": 0.0,
             "feasibility_breakdown": {
                 "error": "Scoring unavailable — malformed LLM response"
             }
         }
       Do NOT raise — degrade gracefully.

    f) On LLM API failure: retry with exponential backoff,
       3 attempts. On exhausted retries: raise the exception.
       (Same pattern as REQ-004 Slice 2 — API failure raises,
       schema failure degrades.)

    g) Clamp all dimension scores to [0, 20] BEFORE summing:
       for dim_name, dim_score in output.model_dump().items():
           clamped = max(0, min(20, dim_score["score"]))
           if clamped != dim_score["score"]:
               logger.warning(
                   f"run_id={state['run_id']} dimension={dim_name} "
                   f"score clamped from {dim_score['score']} to {clamped}"
               )
           dim_score["score"] = clamped

    h) Compute composite score in Python:
       dimension_scores = [d["score"] for d in breakdown.values()
                          if isinstance(d, dict) and "score" in d]
       composite = float(sum(dimension_scores))
       assert abs(composite - sum(dimension_scores)) < 0.01, \
           f"Composite score mismatch: {composite} != {sum(dimension_scores)}"

    i) Build feasibility_breakdown dict:
       {
           "technical_fit":      {"score": X, "rationale": "..."},
           "financial_capacity": {"score": X, "rationale": "..."},
           "timeline":           {"score": X, "rationale": "..."},
           "geographic_scope":   {"score": X, "rationale": "..."},
           "past_experience":    {"score": X, "rationale": "..."},
       }

    j) Return:
       {
           "feasibility_score":     composite,
           "feasibility_breakdown": breakdown,
       }

---

## Dependency versions to use
Use Context7 to confirm:
- with_structured_output() current method signature and
  which LangChain chat model classes support it
- profile_lookup.ainvoke() vs profile_lookup.invoke() —
  confirm async invocation pattern for LangChain tools
  in an async LangGraph node

---

## Rules
- Do NOT modify agents/graph.py, agents/state.py, or
  agents/skills/feasibility_scoring.py.
- Do NOT modify retrieve_risk_relevant_chunks() in retrieval.py —
  add retrieve_scope_relevant_chunks() as a new function only.
- Do NOT create any frontend or test files.
- Do NOT change the return dict keys — "feasibility_score" and
  "feasibility_breakdown" are what REQ-003's aggregator expects.
- Company profile data (financial_capacity, past_projects values)
  must never appear in log statements — only metadata:
  run_id, node_name, composite score, dimension names.
- The assert on composite score must stay in production code,
  not just in tests — it is the mathematical integrity check.
- The two retry strategies must be independent code paths:
  schema failure → max 1 retry → degrade to 0.0
  API failure   → max 3 retries → raise

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (2 files)
2. Run a manual test using a sample company profile and
   sample tender chunks (can be hardcoded test data) and
   show me the ACTUAL output — I want to see real dimension
   scores and rationales, not "it works":
   python -c "
   import asyncio
   from app.agents.nodes.feasibility_scorer import feasibility_scorer_node
   # ... setup state with real company_id and sample chunks
   result = asyncio.run(feasibility_scorer_node(state, {}))
   print('Score:', result['feasibility_score'])
   for dim, data in result['feasibility_breakdown'].items():
       print(f'{dim}: {data[\"score\"]}/20 — {data[\"rationale\"]}')
   "
3. Confirm the assert is present in production code —
   show me the exact assert line
4. Confirm the two retry strategies are independent —
   show me both retry blocks side by side
5. Confirm retrieve_risk_relevant_chunks() is unchanged —
   show me a git diff of retrieval.py

Do not move to Slice 3 until I explicitly tell you to.