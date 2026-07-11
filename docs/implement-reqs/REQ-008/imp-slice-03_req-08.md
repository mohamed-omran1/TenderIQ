Read the following documents before writing any code:
- docs/reqs/REQ-008_Report_Assembler.md

You are implementing **REQ-008 — Slice 3 (API Endpoint) only**.

Slices 1 and 2 are already complete. The following is available:
- report_assembler_node writes state["final_report"] as a dict
  matching ReportOutput schema:
  {
    "go_no_go":              "GO" | "REVIEW" | "DECLINE",
    "effective_score":       float,
    "is_analyst_override":   bool,
    "executive_summary":     str,
    "recommendation":        str, 
    "risk_summary":          [{"category", "severity",
                               "description"}, ...] (max 5),
    "feasibility_highlights": [str, ...] (3-5 items),
    "financial_highlights":  [str, ...] (3-5 items),
    "analyst_note":          str | None,
  }
- After run completes, report is stored in:
  analysis_runs.agent_trace["report_assembler"]["final_report"]
- analysis_runs.state = "complete" when report is ready

---

## Your scope (do not touch anything outside this list)
- app/api/routers/tenders.py (add GET /tenders/{id}/report)
- app/schemas/analysis.py (add ReportResponse +
  RiskSummaryItemResponse)

---

## What to implement

### 1. Pydantic response schemas (add to schemas/analysis.py)

  class RiskSummaryItemResponse(BaseModel):
      category:    str
      severity:    str
      description: str

  class ReportResponse(BaseModel):
      run_id:                 UUID
      tender_id:              UUID
      go_no_go:               str   # "GO" | "REVIEW" | "DECLINE"
      effective_score:        float
      is_analyst_override:    bool
      executive_summary:      str
      recommendation:         str
      risk_summary:           list[RiskSummaryItemResponse]
      feasibility_highlights: list[str]
      financial_highlights:   list[str]
      analyst_note:           str | None
      completed_at:           datetime | None

### 2. GET /tenders/{tender_id}/report

  a) Resolve company_id from API key
  b) Fetch latest analysis_run for this tender_id
     (ORDER BY started_at DESC)
  c) Authorisation: run.company_id must match company_id
     → HTTP 403 if not
  d) If run not found → HTTP 404 "No analysis run found."
  e) If run.state != "complete":
     → HTTP 404 "Report not yet available.
       Current state: {run.state}."
     Use 404 (not 409) — the report page polls this
     endpoint and a 404 is the expected "not ready" signal
  f) Extract report data from agent_trace:
     report_data = run.agent_trace.get(
         "report_assembler", {}
     ).get("final_report", {})
  g) If report_data is empty or missing:
     → HTTP 404 "Report data not found in run trace."
  h) Build and return ReportResponse:
     ReportResponse(
         run_id=run.id,
         tender_id=tender_id,
         go_no_go=report_data.get("go_no_go", "REVIEW"),
         effective_score=report_data.get(
             "effective_score", 0.0),
         is_analyst_override=report_data.get(
             "is_analyst_override", False),
         executive_summary=report_data.get(
             "executive_summary", ""),
         recommendation=report_data.get(
             "recommendation", ""),
         risk_summary=[
             RiskSummaryItemResponse(**r)
             for r in report_data.get("risk_summary", [])
         ],
         feasibility_highlights=report_data.get(
             "feasibility_highlights", []),
         financial_highlights=report_data.get(
             "financial_highlights", []),
         analyst_note=report_data.get("analyst_note"),
         completed_at=run.completed_at,
     )

### 3. Add GET /tenders/{tender_id}/status update
  The existing status endpoint already returns state.
  Add one field to RunStatusResponse in schemas/analysis.py:
    report_available: bool  # True when state="complete"
                            # and agent_trace has report_assembler key

  Update the status endpoint handler to populate this field.
  This lets the frontend know to navigate to the report
  without having to call GET /report just to check.

---

## Rules
- Do NOT modify agents/graph.py, agents/state.py,
  or any node files.
- Do NOT create any frontend or test files.
- Use HTTP 404 (not 409) for "report not yet available" —
  this is the polling signal, consistent with how
  GET /tenders/{id}/financial uses 404 in REQ-006.
- Never return raw financial or risk clause content in
  the report response logs — only metadata may be logged.
- report_data extraction from agent_trace must use .get()
  with defaults at every level — never assume the key
  exists. agent_trace may be an empty dict on a failed run.
- The endpoint must be idempotent — calling it multiple
  times on a complete run must always return the same data.

---

## When you finish
Show me:
1. Full file tree of everything created or modified (2 files)
2. Test the happy path:
   - Run analysis to completion (with HITL approval)
   - Call GET /tenders/{id}/report
   - Show me the full JSON response body
3. Test the "not ready" path:
   - Call GET /report on a run in "awaiting_hitl" state
   - Assert HTTP 404 with message containing "not yet available"
4. Test the "wrong company" path:
   - Call GET /report using a different company's API key
   - Assert HTTP 403
5. Confirm report_available field in GET /status:
   curl GET /tenders/{id}/status on a complete run and
   show me the response — confirm report_available=true

Do not move to Slice 4 until I explicitly tell you to.