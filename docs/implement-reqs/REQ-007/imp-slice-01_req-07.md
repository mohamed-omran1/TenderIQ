Read the following documents before writing any code:
- docs/reqs/REQ-007_HITL_Override_Gate.md
- docs/02_Architecture.md (section 3.3 — Resuming After the HITL Gate)

You are implementing **REQ-007 — Slice 1 (Backend) only**.

REQ-003 through REQ-006 are complete. The following is available:
- app/agents/graph.py → compiled graph with interrupt_before=
  ["report_assembler"] and AsyncPostgresCheckpointer
- analysis_runs table → has state column, feasibility_score column
- The run_graph() background task in routers/tenders.py sets
  state = "awaiting_hitl" after graph.astream() pauses
- TenderState has hitl_approved: bool and
  hitl_override_score: float | None (defined in REQ-003,
  activated here for the first time)

---

## Your scope (do not touch anything outside this list)
- app/db/models.py (add HITLOverride ORM model only)
- alembic/versions/xxxx_create_hitl_overrides_table.py
- app/api/routers/tenders.py (add POST /approve + POST /override)
- app/schemas/analysis.py (add ApproveRequest, OverrideRequest,
  HITLResponse)

---

## What to implement

### 1. Alembic migration — hitl_overrides table
Columns:
  id:                UUID PK, server default gen_random_uuid()
  run_id:            UUID FK → analysis_runs.id, not null
                     UNIQUE constraint — one override per run
  analyst_company_id: UUID FK → companies.id, not null
  action:            VARCHAR not null — "approved" | "overridden"
  original_score:    FLOAT not null
  overridden_score:  FLOAT nullable
  justification:     TEXT nullable
  created_at:        TIMESTAMP WITH TIMEZONE server default now()

Indexes:
  - CREATE INDEX on (run_id) — fast lookup per run
  - The UNIQUE constraint on run_id acts as a natural index

### 2. SQLAlchemy ORM model — HITLOverride
  class HITLOverride(Base):
      __tablename__ = "hitl_overrides"
      id:                 UUID PK
      run_id:             UUID FK → analysis_runs.id (UNIQUE)
      analyst_company_id: UUID FK → companies.id
      action:             str
      original_score:     float
      overridden_score:   float | None
      justification:      str | None
      created_at:         datetime

  Relationship: HITLOverride.run → AnalysisRun (one-to-one)
  Back-reference: AnalysisRun.hitl_override → HITLOverride | None

### 3. Pydantic schemas (add to app/schemas/analysis.py)

  class ApproveRequest(BaseModel):
      justification: str | None = None

  class OverrideRequest(BaseModel):
      overridden_score: float = Field(
          ge=0.0, le=100.0,
          description="Analyst-adjusted feasibility score (0-100)"
      )
      justification: str = Field(
          min_length=10,
          description="Required when overriding the AI score"
      )

  class HITLResponse(BaseModel):
      run_id:           UUID
      action:           str
      original_score:   float
      overridden_score: float | None
      message:          str

### 4. POST /tenders/{tender_id}/approve

  a) Resolve company_id from API key
  b) Fetch latest analysis_run for this tender_id
  c) Authorisation: run.company_id must match company_id
     → HTTP 403 if not
  d) State check: run.state must be "awaiting_hitl"
     → HTTP 409 f"Run is not awaiting review.
       Current state: {run.state}." if not
  e) Check no existing hitl_overrides row for this run_id
     → HTTP 409 "This run has already been reviewed." if exists
  f) Write hitl_overrides row:
     action="approved",
     original_score=run.feasibility_score,
     overridden_score=None,
     analyst_company_id=company_id,
     justification=request.justification
  g) Update analysis_runs.state = "resuming" (intermediate
     state so double-click is impossible even under race)
  h) Commit the DB writes BEFORE launching background task
  i) Launch background task: _resume_graph(run_id, override_score=None)
  j) Return HTTP 202 HITLResponse immediately

### 5. POST /tenders/{tender_id}/override

  a–e) Same validation as /approve
  f) Additional validation:
     overridden_score: 0.0–100.0 (Pydantic handles this)
     justification: min_length=10 (Pydantic handles this)
  g) Write hitl_overrides row:
     action="overridden",
     original_score=run.feasibility_score,
     overridden_score=request.overridden_score,
     analyst_company_id=company_id,
     justification=request.justification
  h) Update analysis_runs.state = "resuming"
  i) Commit BEFORE launching background task
  j) Launch background task:
     _resume_graph(run_id, override_score=request.overridden_score)
  k) Return HTTP 202 HITLResponse

### 6. _resume_graph() background task
Implement as a private async function in routers/tenders.py:

  async def _resume_graph(run_id: UUID,
                           override_score: float | None):
    try:
      config = {"configurable": {"thread_id": str(run_id)}}

      # Inject approval into checkpoint state
      update_values = {"hitl_approved": True}
      if override_score is not None:
          update_values["hitl_override_score"] = override_score

      await graph.aupdate_state(config, update_values)

      # Resume from checkpoint — None = resume, not new run
      async for event in graph.astream(None, config):
          node_name = list(event.keys())[0]
          await db.execute(
              update(AnalysisRun)
              .where(AnalysisRun.id == run_id)
              .values(agent_trace=AnalysisRun.agent_trace.concat(
                  {node_name: event[node_name]}
              ))
          )

      # Graph completed
      await db.execute(
          update(AnalysisRun)
          .where(AnalysisRun.id == run_id)
          .values(state="complete",
                  completed_at=func.now())
      )
      await db.commit()

    except Exception as e:
      await db.execute(
          update(AnalysisRun)
          .where(AnalysisRun.id == run_id)
          .values(state="failed",
                  error_reason=f"Resume failed: {str(e)}")
      )
      await db.commit()
      # hitl_overrides row is NOT deleted on failure —
      # the audit log is preserved even if resume fails

---

## Dependency versions to use
Use Context7 to confirm:
- graph.aupdate_state() current method signature —
  confirm it accepts a dict of values to merge into
  checkpoint state (not replace the entire state)
- graph.astream(None, config) — confirm None as first
  arg is the correct resume pattern for the installed
  LangGraph version (this changes between versions)

---

## Rules
- Do NOT modify agents/graph.py or agents/state.py.
- Do NOT create any frontend files.
- Do NOT create test files — that is Slice 4.
- The "resuming" intermediate state must be set and
  committed BEFORE the background task launches —
  this prevents double-approval race conditions.
- hitl_overrides rows must NEVER be updated or deleted
  anywhere in this codebase — write-once only.
- original_score must be read from analysis_runs.feasibility_score
  at the moment of the HITL action — never recalculated.
- justification text must never appear in any log statement.
- The background task must use its own AsyncSession —
  not the request session (consistent with REQ-003 pattern).

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (4 files)
2. Run the migration:
   alembic upgrade head
   Confirm hitl_overrides table created with UNIQUE on run_id
3. Test Flow A (approve as-is):
   - Trigger analysis on a ready tender, wait for awaiting_hitl
   - POST /tenders/{id}/approve
   - Assert HTTP 202 returned
   - Poll GET /tenders/{id}/status until state = "complete"
   - Query DB: SELECT action, original_score, overridden_score
     FROM hitl_overrides WHERE run_id = '<run_id>'
   Show actual query output
4. Test Flow B (override):
   - Same setup, POST /tenders/{id}/override with score=85.0
   - Assert HTTP 202
   - Read checkpoint directly to confirm hitl_override_score=85.0:
     python -c "
     import asyncio
     from app.agents.graph import graph
     config = {'configurable': {'thread_id': '<run_id>'}}
     async def check():
         state = await graph.aget_state(config)
         print('hitl_override_score:',
               state.values.get('hitl_override_score'))
     asyncio.run(check())
     "
5. Test double-approve returns 409:
   - Approve a run, then try to approve it again immediately
   - Assert second call returns HTTP 409
6. Confirm graph.astream(None, config) is used for resume
   — show me the exact line in _resume_graph()

Do not move to Slice 2 until I explicitly tell you to.