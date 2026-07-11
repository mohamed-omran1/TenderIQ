Read the following documents before writing any code:
- docs/reqs/REQ-008_Report_Assembler.md
- docs/02_Architecture.md (section 3.3 — Resuming After
  the HITL Gate)

You are implementing **REQ-008 — Slice 2 (Node Logic) only**.

Slice 1 is already complete. The following is available:
- app/agents/skills/report_synthesis.py →
  GoNoGo enum, ReportOutput schema, compute_go_no_go(),
  GO_NO_GO_THRESHOLDS, REPORT_SYNTHESIS_PROMPT,
  REPORT_FEW_SHOT_EXAMPLES, FALLBACK_REPORT

REQ-003 through REQ-007 are complete:
- app/agents/state.py → TenderState has:
    hitl_approved: bool
    hitl_override_score: float | None
    feasibility_score: float | None
    feasibility_breakdown: dict | None
    risk_findings: list[dict]
    financial_summary: dict
    aggregated_results: dict | None
    final_report: str | None  ← this node writes here
- app/agents/graph.py → report_assembler wired after
  aggregator with interrupt_before gate (DO NOT MODIFY)
- app/middleware/cost_tracker.py → CostTrackingHandler

The current stub at app/agents/nodes/report_assembler.py:
  async def report_assembler_node(state, config) -> dict:
      logger.info(f"[STUB] report_assembler for {state['run_id']}")
      return {"final_report": "STUB REPORT — REQ-008 pending"}

---

## Your scope (do not touch anything outside this list)
- app/agents/nodes/report_assembler.py (replace stub logic)

---

## What to implement

  async def report_assembler_node(
      state: TenderState,
      config: RunnableConfig
  ) -> dict:

### Step 1 — Determine effective score
  # CRITICAL: use "is not None" not falsy check
  # hitl_override_score=0.0 is valid and must be used
  if state["hitl_override_score"] is not None:
      effective_score = state["hitl_override_score"]
      is_analyst_override = True
      ai_score = state["feasibility_score"] or 0.0
  else:
      effective_score = state["feasibility_score"] or 0.0
      is_analyst_override = False
      ai_score = effective_score

### Step 2 — Compute Go/No-Go in Python (never by LLM)
  from app.agents.skills.report_synthesis import (
      compute_go_no_go, GoNoGo
  )
  go_no_go: GoNoGo = compute_go_no_go(effective_score)

### Step 3 — Build report context for the LLM
  aggregated = state.get("aggregated_results") or {}
  risk_findings = aggregated.get("risk_findings", [])
  feasibility_breakdown = aggregated.get(
      "feasibility_breakdown", {}
  )
  financial_summary = aggregated.get("financial_summary", {})

  # Top 5 risks by severity for context
  severity_order = {"critical": 0, "high": 1,
                    "medium": 2, "low": 3}
  top_risks = sorted(
      risk_findings,
      key=lambda r: severity_order.get(r.get("severity","low"), 3)
  )[:5]

  report_context = {
      "effective_score":      effective_score,
      "go_no_go":             go_no_go.value,
      "is_analyst_override":  is_analyst_override,
      "ai_score":             ai_score if is_analyst_override
                              else None,
      "top_risks":            top_risks,
      "feasibility_breakdown": feasibility_breakdown,
      "financial_summary":    financial_summary,
      "source_languages":     state.get("source_languages", []),
  }

### Step 4 — LLM call with structured output
  Use with_structured_output(ReportOutput) — confirm exact
  syntax via Context7 before writing.

  Attach CostTrackingHandler(
      run_id=state["run_id"],
      node_name="report_assembler",
      db=<session>
  ) to callbacks.

  Pass REPORT_FEW_SHOT_EXAMPLES as prior messages.

  User content: formatted string of report_context dict,
  clearly labelled with section headers for each field.

### Step 5 — Schema validation failure handling
  Retry once on malformed output.
  On second failure:
    logger.warning(
        f"run_id={state['run_id']} report_assembler "
        f"schema validation failed after retry — "
        f"using fallback report"
    )
    fallback = dict(FALLBACK_REPORT)
    fallback["effective_score"] = effective_score
    fallback["go_no_go"] = go_no_go.value
    return {"final_report": fallback}

### Step 6 — LLM API failure handling
  Retry with exponential backoff, 3 attempts.
  On exhausted retries:
    logger.error(
        f"run_id={state['run_id']} report_assembler "
        f"LLM API failed after 3 retries — "
        f"using fallback report"
    )
    fallback = dict(FALLBACK_REPORT)
    fallback["effective_score"] = effective_score
    fallback["go_no_go"] = go_no_go.value
    return {"final_report": fallback}
  # DO NOT RAISE — fallback always returned, never exception

### Step 7 — Return
  On successful LLM output:
    report_dict = output.model_dump()
    logger.info(
        f"run_id={state['run_id']} report_assembler "
        f"complete — go_no_go={go_no_go.value} "
        f"score={effective_score} "
        f"override={is_analyst_override}"
    )
    return {"final_report": report_dict}

---

## Dependency versions to use
Use Context7 to confirm:
- with_structured_output(ReportOutput) current syntax —
  confirm nested Pydantic schema support for the installed
  LangChain version (ReportOutput contains list[RiskSummaryItem])
- CostTrackingHandler callback attachment pattern —
  consistent with REQ-004/005/006 node implementations

---

## Rules
- Do NOT modify agents/graph.py or agents/state.py.
- Do NOT modify agents/skills/report_synthesis.py.
- Do NOT create any frontend or test files.
- The "is not None" check for hitl_override_score is
  NON-NEGOTIABLE — never use "if state['hitl_override_score']:"
  because 0.0 is falsy in Python and would be treated as
  None incorrectly.
- This node must NEVER raise an exception under any
  circumstances — both failure paths (schema + API) must
  return a fallback dict, never propagate.
- The fallback report must always have effective_score and
  go_no_go set to the Python-computed values — never use
  the FALLBACK_REPORT constants directly without updating
  these two fields.
- financial_summary values must never appear in log
  statements — only metadata (run_id, go_no_go, score,
  override flag) may be logged.
- compute_go_no_go() must be imported from the skill
  package — never re-implemented in this file.

---

## When you finish
Show me:
1. Full contents of app/agents/nodes/report_assembler.py
2. Confirm the "is not None" check — show me the exact
   line that determines effective_score
3. Run a manual test to verify Go/No-Go is computed
   in Python, not by LLM:
   python -c "
   from app.agents.skills.report_synthesis import compute_go_no_go
   # Simulate what the node does
   hitl_override_score = 0.0   # analyst set to zero
   feasibility_score = 75.0    # AI score was 75 (GO)
   effective_score = hitl_override_score if \
       hitl_override_score is not None else feasibility_score
   go_no_go = compute_go_no_go(effective_score)
   print('effective_score:', effective_score)
   print('go_no_go:', go_no_go.value)
   # Expected: effective_score=0.0, go_no_go=DECLINE
   # (analyst explicitly set 0, overrides AI's 75)
   "
4. Run a full end-to-end test (with real HITL approval):
   - Upload sample tender, trigger analysis
   - Wait for awaiting_hitl
   - POST /approve
   - Wait for complete
   - Check agent_trace for report_assembler key:
     python -c "
     import asyncio, json
     from sqlalchemy import select
     from app.db.models import AnalysisRun
     # ... fetch run and print agent_trace['report_assembler']
     "
   Show me the actual report_assembler output in agent_trace
5. Confirm this node never raises — show me both
   fallback return statements (schema failure + API failure)

Do not move to Slice 3 until I explicitly tell you to.