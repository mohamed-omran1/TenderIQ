Read the following documents before writing any code:
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md

You are implementing **REQ-005 — Slice 3 (Persistence) only**.

Slices 1 and 2 are already complete. The following is available:
- feasibility_scorer_node returns:
  {
    "feasibility_score":     float (0.0–100.0),
    "feasibility_breakdown": {
      "technical_fit":      {"score": int, "rationale": str},
      "financial_capacity": {"score": int, "rationale": str},
      "timeline":           {"score": int, "rationale": str},
      "geographic_scope":   {"score": int, "rationale": str},
      "past_experience":    {"score": int, "rationale": str},
    }
  }
- analysis_runs table already has a feasibility_score FLOAT
  column (from REQ-003 migration) — no new migration needed
- REQ-004 Slice 3 already added risk_findings INSERT inside
  run_graph() background task in routers/tenders.py, committed
  atomically with analysis_runs.state = "awaiting_hitl"

---

## Your scope (do not touch anything outside this list)
- app/api/routers/tenders.py (one addition only)

---

## What to implement

### Single change: extend the atomic commit block in run_graph()

Find the existing atomic commit block in run_graph() that was
added by REQ-004 Slice 3. It currently looks like this:

  # REQ-004: persist risk findings + state transition atomically
  final_checkpoint = await graph.aget_state(config)
  findings_dicts = final_checkpoint.values.get("risk_findings", [])

  if findings_dicts:
      await db.execute(insert(RiskFindingModel).values([...]))

  await db.execute(
      update(AnalysisRun)
      .where(AnalysisRun.id == run_id)
      .values(state="awaiting_hitl")
  )
  await db.commit()  # ← single commit covers both operations

Extend this block to ALSO write feasibility_score in the
same UPDATE statement — not a separate UPDATE, not a separate
commit. Modify the existing .values() call:

  await db.execute(
      update(AnalysisRun)
      .where(AnalysisRun.id == run_id)
      .values(
          state="awaiting_hitl",
          feasibility_score=final_checkpoint.values.get(
              "feasibility_score"
          ),
      )
  )
  await db.commit()

That is the only change. The commit remains singular — all
three operations (risk_findings INSERT, feasibility_score UPDATE,
state transition) happen in one atomic commit.

Also update GET /tenders/{id}/status to include
feasibility_score in its response:
  - Add feasibility_score: float | None to RunStatusResponse
    in app/schemas/analysis.py
  - Return analysis_run.feasibility_score in the endpoint handler

---

## Rules
- Do NOT modify agents/nodes/feasibility_scorer.py.
- Do NOT modify agents/state.py or agents/graph.py.
- Do NOT create any new migrations — analysis_runs.feasibility_score
  column already exists from REQ-003.
- Do NOT create a separate commit for feasibility_score —
  it must share the SAME db.commit() as risk_findings and
  the state transition. Three operations, one commit.
- Do NOT create any frontend or test files.
- Do NOT touch any other endpoint or router beyond the two
  changes described above.

---

## When you finish
Show me:
1. The exact modified .values() call in run_graph() —
   confirm state, feasibility_score are in the SAME update
2. Confirm there is still exactly ONE db.commit() call
   covering all three operations (risk_findings INSERT +
   feasibility_score UPDATE + state = "awaiting_hitl")
   Show me the full atomic block as it now stands after
   REQ-004 and REQ-005 changes.
3. Run a full end-to-end test:
   - Upload a sample tender
   - Trigger analysis
   - Wait for awaiting_hitl
   - Query DB directly and show me:
     SELECT state, feasibility_score
     FROM analysis_runs
     WHERE id = '<run_id>';
   Expected: state="awaiting_hitl", feasibility_score is
   a float between 0.0 and 100.0 (not null, not 0.0 unless
   the scorer genuinely returned 0)
4. Call GET /tenders/{id}/status and confirm feasibility_score
   appears in the JSON response

Do not move to Slice 4 until I explicitly tell you to.