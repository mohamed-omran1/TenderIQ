Read the following documents before writing any code:
- docs/reqs/REQ-003_LangGraph_Analysis_Run.md
- docs/02_Architecture.md (section 4 — Cost Tracking Middleware)

You are implementing **REQ-003 — Slice 3 (Cost Tracker) only**.

Slices 1 and 2 are already complete. The following are available:
- app/agents/graph.py → compiled graph object
- app/agents/state.py → TenderState
- app/db/models.py → AnalysisRun, Tender, TenderChunk models
- POST /tenders/{id}/analyse → launches graph.astream() in background task
- GET /tenders/{id}/status → returns run state

---

## Your scope (do not touch anything outside this list)
- app/middleware/cost_tracker.py (create)
- app/db/models.py (add LlmCostEvent model only)
- alembic/versions/xxxx_create_llm_cost_events_table.py (new migration)
- app/schemas/analytics.py (create — response schemas only)
- app/api/routers/analytics.py (create — GET /analytics/cost only)
- app/api/routers/tenders.py (one change only: wire CostTrackingHandler
  into the background task — no other changes)

---

## What to implement

### 1. Alembic migration — llm_cost_events table
Columns:
  id:            UUID primary key, server default gen_random_uuid()
  run_id:        UUID FK → analysis_runs.id, not null
  node_name:     VARCHAR not null
  model:         VARCHAR not null
  input_tokens:  INTEGER not null
  output_tokens: INTEGER not null
  cost_usd:      FLOAT not null
  logged_at:     TIMESTAMP WITH TIME ZONE server default now()

Index: CREATE INDEX on (run_id) for fast per-run cost queries.

### 2. SQLAlchemy model — LlmCostEvent
Mapped to llm_cost_events table.
Add relationship: LlmCostEvent.run → AnalysisRun (many-to-one).

### 3. app/middleware/cost_tracker.py

Implement CostTrackingHandler(BaseCallbackHandler):

  class CostTrackingHandler(BaseCallbackHandler):
    def __init__(self, run_id: str, node_name: str, db: AsyncSession):
      self.run_id    = run_id
      self.node_name = node_name
      self.db        = db

    async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
      usage    = response.llm_output.get("token_usage", {})
      model    = response.llm_output.get("model_name", "unknown")
      cost_usd = compute_cost(model, usage)

      await self.db.execute(insert(LlmCostEvent).values(
        run_id        = self.run_id,
        node_name     = self.node_name,
        model         = model,
        input_tokens  = usage.get("prompt_tokens", 0),
        output_tokens = usage.get("completion_tokens", 0),
        cost_usd      = cost_usd,
      ))
      await self.db.commit()

Implement compute_cost(model: str, usage: dict) -> float:
  A pure function (no DB, no I/O) that computes USD cost
  based on model name and token counts.
  Support at minimum these models with their current pricing:
    gpt-4o:              input $2.50 / 1M tokens,  output $10.00 / 1M
    gpt-4o-mini:         input $0.15 / 1M tokens,  output $0.60 / 1M
    gpt-4-turbo:         input $10.00 / 1M tokens, output $30.00 / 1M
    claude-sonnet-4-6:   input $3.00 / 1M tokens,  output $15.00 / 1M
    unknown:             return 0.0 (never raise — unknown models
                         should not crash the pipeline)

  Use Context7 to verify current OpenAI and Anthropic pricing
  before hardcoding these values — pricing changes frequently.

### 4. Wire CostTrackingHandler into the background task
In app/api/routers/tenders.py, modify run_graph() background task
to pass CostTrackingHandler to each node's LLM client config.

The handler is passed via LangChain's callbacks parameter:
  config = {
    "configurable": {"thread_id": str(run_id)},
    "callbacks": [CostTrackingHandler(
      run_id=str(run_id),
      node_name="graph",   # node_name is overridden per-node
      db=background_db_session,
    )]
  }

Note: in REQ-003 stub nodes make no real LLM calls, so
on_llm_end will never fire during testing. The handler must be
wired correctly so it fires automatically in REQ-004+ when real
LLM calls are added to the stub nodes.

### 5. Pydantic schemas in app/schemas/analytics.py
  CostEventSchema:
    run_id:        UUID
    node_name:     str
    model:         str
    input_tokens:  int
    output_tokens: int
    cost_usd:      float
    logged_at:     datetime

  RunCostSummary:
    run_id:        UUID
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    breakdown:     list[CostEventSchema]

  MonthlyCostSummary:
    month:         str   # "2026-06"
    total_cost_usd: float
    total_runs:    int
    avg_cost_per_run: float

  AnalyticsCostResponse:
    per_run:       list[RunCostSummary]
    monthly:       list[MonthlyCostSummary]

### 6. GET /analytics/cost endpoint
In app/api/routers/analytics.py:

  GET /analytics/cost
  Auth: API key (company_id scoped — only this company's runs)
  Query params:
    limit: int = 10 (number of recent runs to return)
    month: str | None (filter by month, format "YYYY-MM")

  Logic:
    a) Fetch last {limit} analysis_runs for this company
       ordered by started_at DESC
    b) For each run, fetch all llm_cost_events and compute
       RunCostSummary (sum tokens, sum cost, list events)
    c) If month param provided: filter runs by started_at month
    d) Compute MonthlyCostSummary grouped by month across all
       returned runs
    e) Return AnalyticsCostResponse

---

## Dependency versions to use
Use Context7 to confirm:
- langchain_core.callbacks BaseCallbackHandler current import path
  and on_llm_end signature (LLMResult type location)
- Current OpenAI and Anthropic token pricing (for compute_cost)

---

## Rules
- Do NOT modify agents/graph.py or agents/state.py.
- Do NOT modify any node files.
- Do NOT create any frontend files.
- Do NOT create test files — that is Slice 5.
- CostTrackingHandler must NEVER raise an exception that
  propagates to the graph — wrap the entire on_llm_end body
  in try/except and log the error silently. A cost logging
  failure must never crash an analysis run.
- compute_cost must be a pure function with no side effects —
  independently unit-testable without a DB or LLM.
- cost_usd, input_tokens, output_tokens must NEVER appear
  in application logs at DEBUG or INFO level — only in the DB.
- The analytics endpoint must be scoped strictly to the
  authenticated company — never return another company's cost data.
- Unknown model names in compute_cost must return 0.0,
  never raise KeyError or ValueError.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (6 files)
2. Run the migration:
   alembic upgrade head
3. Verify compute_cost is a pure function — run this directly:
   python -c "
   from app.middleware.cost_tracker import compute_cost
   print(compute_cost('gpt-4o', {'prompt_tokens': 1000,
     'completion_tokens': 500}))
   # Expected: (1000/1M * 2.50) + (500/1M * 10.00) = 0.0075
   print(compute_cost('unknown-model', {'prompt_tokens': 100,
     'completion_tokens': 50}))
   # Expected: 0.0 — no exception
   "
4. Verify CostTrackingHandler is wired in the background task —
   show me the exact lines in run_graph() where callbacks are passed
5. Verify the analytics endpoint returns correct structure:
   curl -X GET "http://localhost:8000/analytics/cost?limit=5" \
     -H "Authorization: Bearer YOUR_API_KEY"
6. Confirm on_llm_end is wrapped in try/except —
   show me the exact exception handling code

Do not move to Slice 4 until I explicitly tell you to.