Read the following documents before writing any code:
- docs/reqs/REQ-004_Risk_Radar_Node.md

You are implementing **REQ-004 — Slice 3 (Persistence) only**.

Slices 1 and 2 are already complete. The following is available:
- app/agents/skills/risk_clause_extraction.py → RiskFinding schema
- app/agents/nodes/risk_radar.py → returns state["risk_findings"]
  as list[dict] matching RiskFinding.model_dump() shape
- analysis_runs table exists with state column including "awaiting_hitl"
- The graph pauses at interrupt_before=["report_assembler"] and
  analysis_runs.state is set to "awaiting_hitl" by the background
  task in routers/tenders.py

---

## Your scope (do not touch anything outside this list)
- app/db/models.py (add RiskFinding ORM model only)
- alembic/versions/xxxx_create_risk_findings_table.py (new migration)
- app/api/routers/tenders.py (one addition only: persist findings
  when run transitions to "awaiting_hitl" — no other changes)

---

## What to implement

### 1. Alembic migration — risk_findings table
Columns:
  id:                 UUID primary key, server default gen_random_uuid()
  run_id:             UUID FK → analysis_runs.id, not null
  category:           VARCHAR not null
                      allowed: fidic | penalty | lg_bond | termination | other
  severity:           VARCHAR not null
                      allowed: critical | high | medium | low
  clause_text:        TEXT not null
  explanation:        TEXT not null
  source_chunk_index: INTEGER not null
  confidence:         FLOAT not null

Indexes:
  - CREATE INDEX on (run_id) — fast per-run findings queries
  - CREATE INDEX on (run_id, severity) — fast severity-filtered queries

### 2. SQLAlchemy ORM model — RiskFinding
  class RiskFinding(Base):
      __tablename__ = "risk_findings"
      id:                 UUID PK
      run_id:             UUID FK → analysis_runs.id
      category:           str
      severity:           str
      clause_text:        str
      explanation:        str
      source_chunk_index: int
      confidence:         float

  Add relationship: RiskFinding.run → AnalysisRun (many-to-one)
  Add back-reference: AnalysisRun.risk_findings → list[RiskFinding]

### 3. Persist findings when run reaches "awaiting_hitl"
In app/api/routers/tenders.py, inside the run_graph() background
task — in the block that sets analysis_runs.state = "awaiting_hitl"
after graph.astream() completes — add the persistence logic:

  # After graph reaches HITL gate:
  final_checkpoint = await graph.aget_state(config)
  findings_dicts = final_checkpoint.values.get("risk_findings", [])

  if findings_dicts:
      await db.execute(
          insert(RiskFindingModel).values([
              {
                  "run_id": run_id,
                  "category": f["category"],
                  "severity": f["severity"],
                  "clause_text": f["clause_text"],
                  "explanation": f["explanation"],
                  "source_chunk_index": f["source_chunk_index"],
                  "confidence": f["confidence"],
              }
              for f in findings_dicts
          ])
      )

  await db.execute(update(AnalysisRun)
      .where(AnalysisRun.id == run_id)
      .values(state="awaiting_hitl"))

  await db.commit()

Critical ordering: the INSERT into risk_findings and the UPDATE
of analysis_runs.state to "awaiting_hitl" must be in the SAME
db.commit() call — never commit the state change before the
findings are written. If one fails, both must roll back.

### 4. Add GET endpoint for findings
In app/api/routers/tenders.py:

  GET /tenders/{tender_id}/findings
  Auth: API key (company_id scoped)

  Logic:
    a) Resolve company_id from API key
    b) Fetch the latest analysis_run for this tender_id
    c) Authorisation: run.company_id must match authenticated company
    d) Query risk_findings WHERE run_id = run.id
       ORDER BY severity (critical first, then high, medium, low),
       then by confidence DESC within each severity group
    e) Return list[RiskFindingResponse]

  RiskFindingResponse Pydantic schema (add to app/schemas/analysis.py):
    id:                 UUID
    category:           str
    severity:           str
    clause_text:        str
    explanation:        str
    source_chunk_index: int
    confidence:         float

  Severity ordering for the query — use a CASE expression:
    ORDER BY CASE severity
      WHEN 'critical' THEN 1
      WHEN 'high'     THEN 2
      WHEN 'medium'   THEN 3
      WHEN 'low'      THEN 4
    END ASC, confidence DESC

---

## Rules
- Do NOT modify agents/nodes/risk_radar.py or agents/state.py.
- Do NOT modify the graph.py file.
- Do NOT create any frontend or test files.
- The INSERT + UPDATE must be atomic — single commit, never
  two separate commits. If findings insert fails, state must
  not move to "awaiting_hitl".
- Never log clause_text or explanation content — only log
  metadata (run_id, finding count) at INFO level.
- The ORM model name is RiskFinding — be careful not to
  conflict with the Pydantic RiskFinding from the skill package.
  Use an alias at import time where both are needed:
  from app.agents.skills.risk_clause_extraction import (
      RiskFinding as RiskFindingSchema
  )
  from app.db.models import RiskFinding as RiskFindingModel

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (4 files)
2. Run the migration and confirm the table was created:
   alembic upgrade head
3. Run a full analysis end-to-end and verify findings persisted:
   - Upload a sample tender (REQ-001)
   - Trigger analysis (REQ-003)
   - Wait for awaiting_hitl
   - Query the DB directly:
     SELECT id, category, severity, confidence
     FROM risk_findings
     WHERE run_id = '<your_run_id>'
     ORDER BY CASE severity
       WHEN 'critical' THEN 1 WHEN 'high' THEN 2
       WHEN 'medium' THEN 3 WHEN 'low' THEN 4
     END;
   Show me the actual query output.
4. Confirm atomicity — show me the exact code block where INSERT
   and UPDATE share a single db.commit() call
5. Test GET /tenders/{id}/findings and show me the response body
   with at least one real finding

Do not move to Slice 4 until I explicitly tell you to.