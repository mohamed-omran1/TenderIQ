content = """# REQ-007
## Human-in-the-Loop Override Gate

| | |
| --- | --- |
| **Status** | READY FOR IMPLEMENTATION |
| **Sprint** | Week 3 — HITL + Streaming |
| **Priority** | P0 — Blocking. REQ-008 (Report Assembler) cannot run until HITL approval is received. |
| **Dependencies** | REQ-003 complete (interrupt_before gate + Postgres checkpointing). REQ-004/005/006 complete (all three specialist nodes producing real output). analysis_runs.state = "awaiting_hitl" reachable. |
| **Related Docs** | TenderIQ_Architecture_v1.0 §3.3 (Resuming After the HITL Gate)  \\|  TenderIQ_PRD_v1.0 §5.2 |

### Owning Component

| FastAPI Router | LangGraph Resume | hitl_overrides table |
| --- | --- | --- |
| app/api/routers/tenders.py | app/agents/graph.py (no change — resume only) | app/db/models.py |

### Description
Implement the human-in-the-loop override gate that sits between the Results Aggregator and the Report Assembler.

When a run reaches "awaiting_hitl", the analyst reviews the aggregated outputs (risk findings, feasibility score + breakdown, financial commitments) and either approves as-is or submits an override with an adjusted feasibility score and written justification.

On approval or override, the LangGraph graph resumes from its Postgres checkpoint and the Report Assembler node runs.

This REQ does NOT change the graph structure — interrupt_before=["report_assembler"] was defined in REQ-003.

This REQ implements the API endpoints and frontend UI that let the analyst interact with the paused graph, and the resume mechanism that restarts it.

Every HITL decision is recorded in an immutable audit log (hitl_overrides table).

This is a hard requirement for enterprise clients who need accountability trails — the original AI score and the analyst override must both be preserved forever, never overwritten.

### Preconditions
* REQ-003 complete: graph compiled with interrupt_before=["report_assembler"] and AsyncPostgresCheckpointer. A run in "awaiting_hitl" state exists with a valid checkpoint in the DB.
* REQ-004/005/006 complete: the paused checkpoint contains real risk_findings, feasibility_score, feasibility_breakdown, and financial_summary (not stubs).
* The analyst is authenticated via a valid API key for the same company that owns the tender.
* analysis_runs.state = "awaiting_hitl" for the run being acted upon.

### Main Flow

#### Flow A — Approve as-is (no score change)
1. Analyst reviews risk findings, feasibility score, and financial summary on the report page.
2. Analyst clicks "Approve & Generate Report" with no score adjustment.
3. Client calls POST /tenders/{id}/approve with body: `{ justification: "Approved as-is" }` (justification is optional but encouraged).
4. FastAPI validates the run exists, belongs to this company, and is in "awaiting_hitl" state.
5. Backend writes a hitl_overrides row with: original_score = analysis_runs.feasibility_score, overridden_score = null (no change), action = "approved", justification.
6. Backend calls `graph.aupdate_state(config, {"hitl_approved": True})` to inject approval into checkpoint state.
7. Backend calls `graph.astream(None, config)` to resume the graph from the checkpoint — passing None as input signals a resume, not a new run.
8. The Report Assembler node runs. analysis_runs.state transitions to "complete".
9. Client receives HTTP 202 immediately — report assembly runs as a background task.

#### Flow B — Override feasibility score
1. Analyst reviews the feasibility breakdown and disagrees with the AI score.
2. Analyst adjusts the score slider and writes a justification.
3. Client calls POST /tenders/{id}/override with body: `{ overridden_score: 85.0, justification: "..." }`.
4. FastAPI validates: overridden_score must be a float between 0.0 and 100.0. justification is required for overrides (not optional).
5. Backend writes a hitl_overrides row with: original_score = analysis_runs.feasibility_score, overridden_score = 85.0, action = "overridden", justification.
6. Backend calls `graph.aupdate_state(config, {"hitl_approved": True, "hitl_override_score": 85.0})` — both fields injected into checkpoint.
7. Backend resumes graph as in Flow A step 7.
8. The Report Assembler reads hitl_override_score from state and uses it (instead of feasibility_score) as the primary score in the report.
9. Client receives HTTP 202.

### Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| Run is not in "awaiting_hitl" state (already approved, complete, or failed) | HTTP 409 — "Run is not awaiting review. Current state: &lt;state&gt;." | No graph state change. Run continues in its current state. |
| overridden_score outside 0.0-100.0 range | HTTP 422 — "overridden_score must be between 0.0 and 100.0." | No DB write. Run remains in awaiting_hitl. |
| Override submitted without justification | HTTP 422 — "justification is required when overriding the feasibility score." | No DB write. |
| Run belongs to a different company | HTTP 403 — "Not authorised to act on this tender." | No action taken. |
| graph.astream() fails during resume (Report Assembler error) | analysis_runs.state = "failed", error_reason set. hitl_overrides row is retained — the audit log is never deleted even on failure. | Run failed but HITL decision is preserved in audit log. |
| Analyst attempts to approve a run that was already approved (double-click) | HTTP 409 — same as run not in awaiting_hitl. The state check prevents double-resume. | No duplicate graph resume. |

### Postconditions
* On successful approval or override: analysis_runs.state transitions from "awaiting_hitl" to "complete" (after Report Assembler finishes).
* hitl_overrides contains exactly one immutable row for this run.
* The hitl_overrides row is never updated or deleted — it is an append-only audit log.
* If the analyst re-runs analysis on the same tender, a new run_id is created with a new hitl_overrides row.
* If override was submitted: state["hitl_override_score"] in the checkpoint reflects the analyst's score, and the Report Assembler uses this value.
* The original AI score is preserved in hitl_overrides.original_score.
* The graph checkpoint is consumed by the resume — after "complete", the checkpoint is no longer resumable.

### Data Requirements

#### hitl_overrides table

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID PK | server default gen_random_uuid() |
| run_id | UUID FK | → analysis_runs.id. UNIQUE constraint — one override per run. |
| analyst_company_id | UUID FK | → companies.id. Records which company's analyst took the action. |
| action | VARCHAR | "approved" \| "overridden". Never null. |
| original_score | FLOAT | The AI feasibility_score at time of HITL. Never null. |
| overridden_score | FLOAT nullable | Null if action="approved". Analyst's adjusted score if action="overridden". |
| justification | TEXT | Required for "overridden". Optional but stored for "approved". |
| created_at | TIMESTAMP TZ | Server default now(). Immutable — never updated. |

#### Pydantic schemas

```

```text
Saved successfully

```python
class ApproveRequest(BaseModel):
    justification: str | None = None

class OverrideRequest(BaseModel):
    overridden_score: float = Field(ge=0.0, le=100.0)
    justification:    str   = Field(min_length=10, description="Required for overrides")

class HITLResponse(BaseModel):
    run_id:           UUID
    action:           str   # "approved" | "overridden"
    original_score:   float
    overridden_score: float | None
    message:          str   # "Report assembly started"

```

#### LangGraph resume pattern

```python
config = {"configurable": {"thread_id": str(run_id)}}
# Inject approval (and optionally override score) into checkpoint
update_values = {"hitl_approved": True}
if override_score is not None:
    update_values["hitl_override_score"] = override_score
await graph.aupdate_state(config, update_values)
# Resume graph from checkpoint — None = resume, not new run
async for event in graph.astream(None, config):
    node_name = list(event.keys())[0]
    await db.execute(update(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .values(agent_trace=AnalysisRun.agent_trace.concat(
            {node_name: event[node_name]})))
# Graph completes — update state to "complete"
await db.execute(update(AnalysisRun)
    .where(AnalysisRun.id == run_id) 
    .values(state="complete", completed_at=func.now()))
await db.commit()

```

### Non-Functional Requirements

#### Audit integrity

* hitl_overrides rows are immutable — no UPDATE or DELETE is permitted on this table.
* Enforced at the application layer (no update queries) and documented as a constraint.
* original_score must be captured from analysis_runs.feasibility_score at the moment of the HITL action — not recalculated.

#### Performance

* POST /approve and POST /override must respond in under 500ms — graph resume runs as a background task, consistent with REQ-003's /analyse endpoint pattern.

#### Security

* Only the company that owns the tender may approve or override its run — enforced by matching analyst_company_id from API key against tender.company_id.
* justification text must not be logged — it may contain sensitive commercial reasoning.

#### Reliability

* If the Report Assembler fails after HITL approval, the hitl_overrides row is preserved — the analyst's decision is never lost even if the downstream step fails.

### Implementation Slices

Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice.

| Slice | Owns | Scope |
| --- | --- | --- |
| 1. Backend | db/models.py (HITLOverride model), alembic migration, routers/tenders.py (POST /approve + POST /override), schemas/analysis.py (ApproveRequest, OverrideRequest, HITLResponse) | Create hitl_overrides table and ORM model. Implement both endpoints with full validation, DB write, graph.aupdate_state(), and background task resume via graph.astream(None, config). Handle all Alternative Flow error responses. |
| 2. Frontend | components/HITLGate.tsx (create), app/tenders/[id]/report/page.tsx (modify — enable the approve button) | Replace the disabled "Approve & Generate Full Report" button with a real HITLGate component: score display with current AI score, optional score adjustment slider (0–100, step 1), justification text area (required when score is changed), Approve button that calls POST /approve or POST /override depending on whether score was adjusted. Show confirmation state after approval. |
| 3. Frontend Polish | components/HITLGate.tsx (extend), lib/api/hitl.ts (create) | Add needs_review warning banner above the gate if any financial commitment has needs_review=True (pull from existing financial data). Add override history display showing original_score vs overridden_score if an override was submitted. Wire POST /approve and POST /override via typed API client in lib/api/hitl.ts. |
| 4. QA | tests/test_hitl.py | Tests: approve transitions run to complete, override injects correct score into checkpoint, duplicate approve returns 409, wrong company returns 403, non-awaiting_hitl run returns 409, invalid overridden_score returns 422, missing justification on override returns 422, hitl_overrides row is immutable (no update possible), original_score captured correctly, graph resumes and report_assembler node runs after approval. |

### Slice Activation Rule

The project owner selects which slice is executed and when — this decision is never delegated to the AI agent. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope. The agent must not expand scope to cover other slices, and must not select the next slice on its own.

### Acceptance Criteria / Definition of Done

* [ ] POST /tenders/{id}/approve transitions run state from "awaiting_hitl" to "complete" (after Report Assembler stub runs) and creates a hitl_overrides row with action="approved".
* [ ] POST /tenders/{id}/override with a valid score and justification creates a hitl_overrides row with action="overridden" and injects hitl_override_score into the LangGraph checkpoint state.
* [ ] After override, state["hitl_override_score"] in the checkpoint equals the submitted overridden_score — verified by reading the checkpoint directly after override.
* [ ] Attempting to approve a run not in "awaiting_hitl" state returns HTTP 409.
* [ ] Attempting to override with overridden_score outside [0.0, 100.0] returns HTTP 422.
* [ ] Attempting to override without justification returns HTTP 422.
* [ ] A company cannot approve or override another company's run — returns HTTP 403.
* [ ] hitl_overrides.original_score equals the analysis_runs.feasibility_score at the time of the HITL action — never a recalculated value.
* [ ] hitl_overrides rows cannot be updated or deleted — verified by attempting an UPDATE directly and confirming it is blocked at the application layer.
* [ ] HITLGate frontend component shows the current AI feasibility score, allows score adjustment, requires justification when score is changed, and calls the correct endpoint.
* [ ] After approval, the report page shows a "Report generation in progress" state and transitions to the final report when complete.

### Document Control

The hitl_override_score field in TenderState was defined in REQ-003 and is now activated here. The Report Assembler (REQ-008) must read hitl_override_score first and fall back to feasibility_score if null — never read feasibility_score directly without checking for an override.

