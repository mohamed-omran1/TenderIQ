Read the following documents before writing any code:
- docs/reqs/REQ-003_LangGraph_Analysis_Run.md
- docs/02_Architecture.md (section 2 — Request Lifecycle)

You are implementing **REQ-003 — Slice 2 (API Endpoints) only**.

Slice 1 is already complete. The following are available and working:
- app/agents/state.py → TenderState TypedDict
- app/agents/graph.py → compiled graph object (import as: from app.agents.graph import graph)
- app/agents/nodes/ → all nodes including stubs
- Existing endpoints: POST /tenders/upload, GET /company-profile, PUT /company-profile

---

## Your scope (do not touch anything outside this list)
- app/api/routers/tenders.py (add 2 new endpoints only)
- app/db/models.py (add AnalysisRun model only)
- alembic/versions/xxxx_create_analysis_runs_table.py (new migration)
- app/schemas/analysis.py (create — Pydantic schemas for this REQ)

---

## What to implement

### 1. Alembic migration — analysis_runs table
Columns:
  id:                UUID primary key, server default gen_random_uuid()
  tender_id:         UUID FK → tenders.id, not null
  company_id:        UUID FK → companies.id, not null
  state:             VARCHAR not null, default "pending"
                     allowed values: pending | running | awaiting_hitl
                     | complete | failed
  feasibility_score: FLOAT nullable
  agent_trace:       JSONB default {}
  aggregated_results: JSONB nullable
  error_reason:      TEXT nullable
  started_at:        TIMESTAMP WITH TIME ZONE server default now()
  completed_at:      TIMESTAMP WITH TIME ZONE nullable

### 2. SQLAlchemy model — AnalysisRun
Mapped to analysis_runs table.
Add a relationship: AnalysisRun.tender → Tender (many-to-one).

### 3. Pydantic schemas in app/schemas/analysis.py
  AnalyseResponse:
    run_id: UUID
    status: str

  RunStatusResponse:
    run_id: UUID
    state: str
    started_at: datetime
    completed_at: datetime | None
    error_reason: str | None
    agent_trace: dict

### 4. POST /tenders/{tender_id}/analyse
Full implementation:

  a) Resolve company_id from API key (use existing auth dependency)

  b) Fetch the tender — if not found: HTTP 404 "Tender not found."

  c) Authorisation check — if tender.company_id != company_id:
     HTTP 403 "Not authorised to analyse this tender."

  d) Status check — if tender.status != "ready":
     HTTP 409 f"Tender is not ready for analysis.
     Current status: {tender.status}."

  e) Duplicate run check — query analysis_runs for any row where
     tender_id = this tender AND state IN ("pending", "running",
     "awaiting_hitl"). If found:
     HTTP 409 "An analysis run is already in progress for this tender."

  f) Create analysis_runs row with state = "pending", return
     HTTP 202 AnalyseResponse immediately.

  g) Launch background task:

     async def run_graph(run_id, tender_id, company_id, chunks):
       try:
         # Update state to running
         await db.execute(update(AnalysisRun)
           .where(AnalysisRun.id == run_id)
           .values(state="running"))

         initial_state = TenderState(
           tender_id=str(tender_id),
           run_id=str(run_id),
           company_id=str(company_id),
           chunks=chunks,           # list of dicts from tender_chunks
           supervisor_ready=False,
           risk_findings=[],
           feasibility_score=None,
           feasibility_breakdown=None,
           financial_summary=None,
           aggregated_results=None,
           hitl_approved=False,
           hitl_override_score=None,
           final_report=None,
           token_usage=[],
           source_languages=[],
         )

         config = {"configurable": {"thread_id": str(run_id)}}

         async for event in graph.astream(initial_state, config):
           node_name = list(event.keys())[0]
           # Append node output to agent_trace
           await db.execute(update(AnalysisRun)
             .where(AnalysisRun.id == run_id)
             .values(agent_trace=AnalysisRun.agent_trace.concat(
               {node_name: event[node_name]}
             )))

         # Graph paused at HITL gate
         await db.execute(update(AnalysisRun)
           .where(AnalysisRun.id == run_id)
           .values(state="awaiting_hitl"))

       except Exception as e:
         await db.execute(update(AnalysisRun)
           .where(AnalysisRun.id == run_id)
           .values(state="failed", error_reason=str(e)))

  h) The chunks list passed to initial_state must be fetched from
     tender_chunks before launching the background task
     (not inside the background task — the DB session scope matters).

### 5. GET /tenders/{tender_id}/status
  a) Resolve company_id from API key
  b) Fetch the latest analysis_runs row for this tender_id
     ordered by started_at DESC
  c) Authorisation: run.company_id must match authenticated company_id
  d) Return HTTP 200 RunStatusResponse
  e) If no run exists yet: HTTP 404 "No analysis run found for this tender."

---

## Dependency versions to use
Use Context7 to confirm current FastAPI BackgroundTasks API and
async SQLAlchemy update() syntax before writing.

---

## Rules
- Do NOT modify agents/graph.py, agents/state.py, or any node files.
- Do NOT create any frontend files.
- Do NOT create test files — that is Slice 5.
- Do NOT import graph at module level in the router —
  import inside the endpoint function or use FastAPI dependency
  injection to avoid circular imports.
- The background task must use its own AsyncSession (not the
  request session which closes when the response is sent).
- chunks must be serialised to list[dict] before passing to
  TenderState — do not pass SQLAlchemy ORM objects into the graph.
- run_id is always generated server-side (uuid4) — never from client.
- agent_trace JSONB update must be an atomic append operation —
  not a read-modify-write pattern that risks race conditions.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (4 files)
2. Run the migration and confirm the table was created:
   alembic upgrade head
3. Test the happy path manually:
   - Upload a tender (REQ-001)
   - Call POST /tenders/{id}/analyse
   - Assert HTTP 202 comes back in under 500ms
   - Poll GET /tenders/{id}/status 3 times (2s apart)
   - Show the state transitions: pending → running → awaiting_hitl
4. Confirm the duplicate-run check works:
   - Call POST /analyse twice on the same tender
   - Second call must return HTTP 409
5. Confirm the background task uses a separate DB session
   from the request session (show me the session creation code)

Do not move to Slice 3 until I explicitly tell you to.