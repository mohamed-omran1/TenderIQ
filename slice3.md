REQ-005 Slice 3 — Done
1. Modified .values() call in run_graph() (app/routers/tenders.py:160-169)
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
state and feasibility_score are in the same update().values() call.
2. Full atomic commit block — exactly ONE db.commit()
app/routers/tenders.py:137-171 — three operations, one transaction:
final_checkpoint = await graph.aget_state(config)
findings_dicts = (
    final_checkpoint.values.get("risk_findings", [])
    if final_checkpoint is not None
    else []
) or []

if findings_dicts:
    await db.execute(
        insert(RiskFinding).values([
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
# Single commit — INSERT (if any) + UPDATE land atomically.
await db.commit()
Three ops: risk_findings INSERT + feasibility_score UPDATE + state = "awaiting_hitl" UPDATE → one db.commit(). Confirmed.
3. Direct DB query
$ SELECT state, feasibility_score FROM analysis_runs
  WHERE id = '138a7946-2d18-4e69-b8de-5edbdc6d1e27';
     state     | feasibility_score 
---------------+-------------------
 awaiting_hitl |                76
(1 row)
state = "awaiting_hitl", feasibility_score = 76.0 (float, in [0.0, 100.0]).
4. GET /tenders/{id}/status JSON response
{
  "run_id": "138a7946-2d18-4e69-b8de-5edbdc6d1e27",
  "state": "awaiting_hitl",
  "started_at": "2026-07-02T19:49:04.450074Z",
  "completed_at": null,
  "error_reason": null,
  "feasibility_score": 76.0,
  "agent_trace": { ... }
}
feasibility_score: 76.0 appears in the response. Same atomic commit also wrote 2 risk_findings rows (proves the three ops land together — see risk_findings count = 2 in the same trace).
Files touched (exactly as scoped)
- backend/app/routers/tenders.py — extended the existing atomic block (one .values() call now writes both state and feasibility_score); added feasibility_score=run.feasibility_score to the RunStatusResponse constructor.
- backend/app/schemas/analysis.py — added feasibility_score: float | None = None to RunStatusResponse.
No migrations, no new files, no agent/graph/state changes.