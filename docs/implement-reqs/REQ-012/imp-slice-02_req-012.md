Read the following documents before writing any code:
- docs/reqs/REQ-012_Evaluation_Harness.md

You are implementing **REQ-012 — Slice 2 (API Endpoint) only**.

Slice 1 is already complete. The following is available:
- eval/schemas.py → CategoryMetrics, RiskRadarEvalResult,
  ScorerConsistencyResult, EvalRunResult Pydantic schemas
- eval/run_eval.py → run_risk_radar_eval(),
  run_scorer_consistency_eval(), match_findings(),
  compute_overlap() all working
- eval/labelled_sample_tender.json → real or placeholder

---

## Your scope (do not touch anything outside this list)
- app/api/routers/eval.py (create)
- app/db/models.py (add EvalResult ORM model only)
- alembic/versions/xxxx_create_eval_results_table.py
- app/schemas/eval.py (create — API request/response schemas)
- app/main.py (register eval router — one line only)

---

## What to implement

### 1. Alembic migration — eval_results table
Columns:
  id:             UUID PK, server default gen_random_uuid()
  company_id:     UUID FK → companies.id, not null
  tender_id:      UUID FK → tenders.id, not null
  result:         JSONB not null
  overall_status: VARCHAR not null
  total_cost_usd: FLOAT not null default 0.0
  run_at:         TIMESTAMP TZ server default now()

Indexes:
  - CREATE INDEX on (company_id, run_at DESC)
    for fast "last 10 evals for this company" queries

### 2. SQLAlchemy ORM model — EvalResult
  class EvalResult(Base):
      __tablename__ = "eval_results"
      id:             UUID PK
      company_id:     UUID FK → companies.id
      tender_id:      UUID FK → tenders.id
      result:         dict  # JSONB
      overall_status: str
      total_cost_usd: float
      run_at:         datetime

### 3. API request/response schemas (app/schemas/eval.py)

  class EvalRequest(BaseModel):
      tender_id:             UUID
      run_risk_radar:        bool = True
      run_scorer_consistency: bool = False

  class EvalResultResponse(BaseModel):
      id:             UUID
      tender_id:      UUID
      overall_status: str
      total_cost_usd: float
      run_at:         datetime
      result:         dict  # full EvalRunResult as dict

### 4. Admin authentication dependency

  Create a FastAPI dependency: require_admin_key()
  - Reads X-Admin-Key header
  - Compares against ADMIN_API_KEY environment variable
    (add to .env.example: ADMIN_API_KEY=)
  - If missing or wrong: HTTP 403
    "Admin access required."
  - This is a separate auth from the company API key —
    company keys must NOT pass this check even if valid.

### 5. POST /eval/run

  @router.post("/eval/run", dependencies=[Depends(require_admin_key)])
  async def run_eval(
      request: EvalRequest,
      db: AsyncSession = Depends(get_db),
  ) -> EvalResultResponse:

  a) Validate tender exists and is in "ready" state:
     → HTTP 409 if not ready

  b) Validate at least one eval type is enabled:
     if not request.run_risk_radar and
        not request.run_scorer_consistency:
       → HTTP 422 "At least one eval type must be enabled."

  c) Load eval/labelled_sample_tender.json:
     import json, pathlib
     json_path = pathlib.Path("eval/labelled_sample_tender.json")
     ground_truth = json.loads(json_path.read_text())
     labelled_findings = ground_truth.get("labelled_findings", [])

  d) Generate eval_run_id = f"eval-{uuid4()}"

  e) Run selected evals (import from eval/run_eval.py):
     risk_result = None
     scorer_result = None

     if request.run_risk_radar:
       if not labelled_findings:
         notes = "No labelled ground truth available."
       else:
         risk_result = await run_risk_radar_eval(
             str(request.tender_id),
             labelled_findings,
             eval_run_id,
         )

     if request.run_scorer_consistency:
       # Get company_id from the tender
       tender = await db.get(Tender, request.tender_id)
       scorer_result = await run_scorer_consistency_eval(
           str(request.tender_id),
           str(tender.company_id),
           eval_run_id,
       )

  f) Compute total_cost_usd from llm_cost_events:
     SELECT SUM(cost_usd) FROM llm_cost_events
     WHERE run_id LIKE 'eval-%'
     AND run_id LIKE f'{eval_run_id}%'

  g) Determine overall_status:
     statuses = []
     if risk_result: statuses.append(risk_result.pass_fail)
     if scorer_result: statuses.append(scorer_result.pass_fail)
     if not statuses: overall_status = "NO_DATA"
     elif all(s == "PASS" for s in statuses):
       overall_status = "PASS"
     elif any(s == "FAIL" for s in statuses):
       overall_status = "FAIL"
     else: overall_status = "PARTIAL"

  h) Build EvalRunResult and store in eval_results:
     eval_run = EvalRunResult(
         eval_id=eval_run_id,
         tender_id=str(request.tender_id),
         tender_name=ground_truth.get("tender_name", "Unknown"),
         run_at=datetime.utcnow().isoformat() + "Z",
         risk_radar=risk_result,
         scorer=scorer_result,
         total_cost_usd=total_cost_usd,
         overall_status=overall_status,
         notes=notes if not labelled_findings else None,
     )
     db_row = EvalResult(
         company_id=tender.company_id,
         tender_id=request.tender_id,
         result=eval_run.model_dump(),
         overall_status=overall_status,
         total_cost_usd=total_cost_usd,
     )
     db.add(db_row)
     await db.commit()
     await db.refresh(db_row)

  i) Return EvalResultResponse

### 6. GET /eval/results

  @router.get("/eval/results",
              dependencies=[Depends(require_admin_key)])
  async def get_eval_results(
      limit: int = 10,
      db: AsyncSession = Depends(get_db),
  ) -> list[EvalResultResponse]:

  - Query last {limit} eval_results rows
    ORDER BY run_at DESC
  - No company_id filter — admin sees all companies' evals
  - Return list[EvalResultResponse]

### 7. Register router in app/main.py
  from app.api.routers.eval import router as eval_router
  app.include_router(eval_router, prefix="/eval",
                     tags=["eval"])

---

## Rules
- Do NOT modify eval/run_eval.py or eval/schemas.py.
- Do NOT create any frontend files.
- The admin key check must be a separate dependency —
  never reuse or combine with company API key auth.
- Company API keys must return 403 on /eval/run —
  even valid company keys are not admin keys.
- eval_run_id must always start with "eval-" —
  verified by asserting in the endpoint before any
  LLM calls.
- eval_results rows must never be deleted — append-only
  history log for regression tracking.
- ADMIN_API_KEY must come from environment variable —
  never hardcoded. Add to .env.example.
- The eval endpoint must not affect production
  analysis_runs table — no writes to that table.

---

## When you finish
Show me:
1. Full file tree of everything created or modified (5 files)
2. Run migration:
   alembic upgrade head
   Confirm eval_results table created
3. Test POST /eval/run with admin key:
   curl -X POST http://localhost:8000/eval/run \
     -H "X-Admin-Key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"tender_id": "<uuid>", "run_risk_radar": true}'
   Show actual response body
4. Test company API key returns 403:
   curl -X POST http://localhost:8000/eval/run \
     -H "Authorization: Bearer COMPANY_KEY" \
     -H "Content-Type: application/json" \
     -d '{"tender_id": "<uuid>", "run_risk_radar": true}'
   Assert HTTP 403
5. Test GET /eval/results:
   curl http://localhost:8000/eval/results \
     -H "X-Admin-Key: YOUR_ADMIN_KEY"
   Show response — should include the run from step 3
6. Confirm eval_run_id starts with "eval-":
   SELECT result->>'eval_id' FROM eval_results LIMIT 1;
   Assert starts with "eval-"

Do not move to Slice 3 until I explicitly tell you to.