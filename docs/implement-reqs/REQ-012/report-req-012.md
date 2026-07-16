Read the following documents before writing any code:
- docs/reqs/REQ-012_Evaluation_Harness.md
- Every file you created or modified across Slices 1-3

Generate a structured implementation report for REQ-012.
Do NOT modify any code while generating this report.

---

## Report structure — produce exactly this

### 1. Summary
3 sentences maximum:
  - What REQ-012 delivers (automated accuracy measurement
    harness for Risk Radar recall and Scorer consistency)
  - Why it matters (closes the loop between the 85% recall
    target in the PRD and measurable evidence)
  - What it means for the project (TenderIQ MVP is now
    feature-complete — all 12 REQs implemented)

### 2. Files Created/Modified — grouped by Slice

  Slice 1 — Eval Logic
    eval/schemas.py →
      CategoryMetrics, RiskRadarEvalResult,
      ScorerConsistencyResult, EvalRunResult schemas
    eval/run_eval.py →
      compute_overlap(), match_findings(),
      run_risk_radar_eval(), run_scorer_consistency_eval(),
      CLI with --risk --scorer --output flags
    eval/labelled_sample_tender.json →
      State: REAL ({N} findings) or PLACEHOLDER (0 findings)

  Slice 2 — API Endpoint
    app/api/routers/eval.py →
      POST /eval/run (admin auth),
      GET /eval/results,
      require_admin_key() dependency
    app/db/models.py →
      EvalResult ORM model
    alembic/versions/xxxx_create_eval_results_table.py →
      migration
    app/schemas/eval.py →
      EvalRequest, EvalResultResponse
    app/main.py →
      eval router registered

  Slice 3 — Frontend
    frontend/lib/api/eval.ts →
      runEval(), getEvalResults(), typed schemas,
      AdminAuthError
    frontend/components/EvalResultCard.tsx →
      PASS/FAIL badge, recall/std_dev colouring,
      collapsible per-category and dimension tables
    frontend/app/eval/page.tsx →
      admin eval page, run form, recent results list
    .env.example →
      NEXT_PUBLIC_ADMIN_KEY added

### 3. Acceptance Criteria Verification
Every AC from REQ-012 with actual evidence:

  AC: "POST /eval/run returns structured EvalRunResult
       with recall, precision, F1, per-category breakdown"
  Status: ✅ PASS / ⚠️ PARTIAL / ❌ NOT VERIFIED
  Evidence: (curl output from Slice 2 verification step 3
             — paste actual response)

  AC: "Recall >= 85% on labelled sample tender — or
       documented as FAIL with actual measured recall"
  Status: ✅ PASS (X%) / ❌ FAIL (X%) / ⚠️ NO_DATA
  Evidence: (actual CLI output from Slice 1 verification
             step 4 — paste actual terminal output)
  Note: If NO_DATA (placeholder), this AC cannot be
        verified until a real labelled tender is provided.
        This is acceptable for MVP — document honestly.

  AC: "Scorer consistency std_dev <= 5.0 across 3 runs"
  Status: ✅ PASS (X.X) / ❌ FAIL (X.X) / ⚠️ NOT RUN
  Evidence: (actual CLI output)

  AC: "Eval results stored in eval_results table and
       retrievable via GET /eval/results"
  Status: ✅ PASS
  Evidence: (curl output from GET /eval/results)

  AC: "Placeholder ground truth returns HTTP 200 with
       risk_radar=null and explanatory note"
  Status: ✅ PASS / ❌ NOT VERIFIED
  Evidence: (if tested — show response body)

  AC: "Eval cost events stored with eval- prefix"
  Status: ✅ PASS / ❌ NOT VERIFIED
  Evidence: (SQL query output:
    SELECT run_id FROM llm_cost_events
    WHERE run_id LIKE 'eval-%' LIMIT 3)

  AC: "POST /eval/run is admin-only — company keys
       return 403"
  Status: ✅ PASS
  Evidence: (curl output from Slice 2 verification step 4)

  AC: "eval/run_eval.py exits with code 0 on PASS,
       code 1 on FAIL"
  Status: ✅ PASS / ❌ NOT VERIFIED
  Evidence: (echo $? output from Slice 1 step 6)

### 4. Ground Truth Status — Critical Section
This is the most important section for this REQ.

  eval/labelled_sample_tender.json state:
    ☐ REAL — {N} labelled findings across {M} categories
    ☐ PLACEHOLDER — 0 findings (eval deferred)

  If REAL:
    Tender name: <name>
    Source: <where it came from>
    Findings breakdown:
      fidic:       X clauses
      penalty:     X clauses
      lg_bond:     X clauses
      termination: X clauses
      other:       X clauses
    Actual recall achieved: X%
    Status vs 85% target: PASS / FAIL

  If PLACEHOLDER:
    Action required before production launch:
    1. Obtain a real bilingual tender document
       (public procurement portal, or anonymised client tender)
    2. Manually label all risk clauses in the document
       (category, severity, clause_text, source_chunk_index)
    3. Replace the placeholder JSON with the real findings
    4. Re-run: python eval/run_eval.py --tender-id <uuid> --risk
    5. Verify recall >= 85% before first paid pilot

    This is an OPEN ITEM for the MVP launch checklist.

### 5. Eval Run Output — Actual Results
Paste the ACTUAL terminal output from:
  python eval/run_eval.py \
    --tender-id <uuid> --company-id <uuid> \
    --risk --scorer --output text

Whether it shows real metrics or "no data" — paste as-is.
This is the source of truth for the current accuracy baseline.

### 6. Admin Auth Verification
  Show the ACTUAL require_admin_key() function from
  app/api/routers/eval.py:
  (paste actual code)

  Confirm:
    ☐ Reads X-Admin-Key header (not Authorization)
    ☐ Compares against ADMIN_API_KEY env variable
    ☐ Company API keys return 403 (not pass through)
    ☐ ADMIN_API_KEY in .env.example

### 7. eval_run_id Prefix Verification
  Show actual SQL query and output:
    SELECT run_id FROM llm_cost_events
    WHERE run_id LIKE 'eval-%'
    LIMIT 5;

  Confirm all eval cost events have "eval-" prefix:
    ☐ All rows show "eval-{uuid}" format
    ☐ No production run_ids in eval cost events

### 8. Complete MVP Status — Final Checklist
This is the definitive MVP completion status.
Update each row based on ACTUAL implementation state:

  | REQ     | Feature                  | Doc | Impl | Tests |
  |---------|--------------------------|-----|------|-------|
  | REQ-001 | PDF Upload & Ingestion   | ✅  | ✅   | ✅    |
  | REQ-002 | Company Profile          | ✅  | ✅   | ✅    |
  | REQ-003 | LangGraph Analysis Run   | ✅  | ✅   | ✅    |
  | REQ-004 | Risk Radar               | ✅  | ✅   | ✅    |
  | REQ-005 | Feasibility Scorer       | ✅  | ✅   | ✅    |
  | REQ-006 | Financial Analyst        | ✅  | ✅   | ✅    |
  | REQ-007 | HITL Override Gate       | ✅  | ✅   | ✅    |
  | REQ-008 | Report Assembler         | ✅  | ✅   | ✅    |
  | REQ-009 | WebSocket Streaming      | ✅  | ✅   | ✅    |
  | REQ-010 | LLM Cost Tracking        | ✅  | ✅   | —     |
  | REQ-011 | API Auth + Rate Limit    | ✅  | ✅   | ✅    |
  | REQ-012 | Evaluation Harness       | ✅  | ✅   | —     |

  Update any ✅ that is not actually complete to ⚠️ or ❌.
  Be honest — this table is the MVP launch gate.

### 9. Known Limitations / Deferred Items
  - labelled_sample_tender.json may still be a placeholder
    — see Section 4 for the action plan
  - Eval has no automated test suite (no Slice 4/5 in
    REQ-012) — eval correctness is verified by running
    it against the labelled tender manually
  - Scorer consistency target (std_dev <= 5.0) may need
    tuning based on real-world LLM variability — the
    threshold is a starting point, not a hard requirement
  - Eval page at /eval has no authentication beyond the
    admin key in env — anyone with the URL and key can
    access it (acceptable for MVP, needs proper admin
    auth in v2)
  - Any other limitations noticed during implementation

### 10. Dependency Versions Used
  pip list output for:
    fastapi, SQLAlchemy, alembic, pydantic,
    difflib (stdlib — no version)

  npm list for:
    (confirm no new packages added)

### 11. MVP Launch Checklist
After REQ-012, before the first pilot client:

  ☐ Replace placeholder labelled tender (if applicable)
  ☐ Run eval against real tender: recall >= 85%
  ☐ Set ADMIN_API_KEY in production environment
  ☐ Set all other production env variables
  ☐ Run full test suite: pytest -v (all tests pass)
  ☐ Deploy to Railway (backend) + Vercel (frontend)
  ☐ Verify WebSocket works in production (not just local)
  ☐ Record a 3-5 minute Loom demo of the full pipeline
  ☐ Update GitHub README with public URL

---

## Rules
- Do NOT modify any code while generating this report.
- Section 4 (Ground Truth Status) is the most important —
  be completely honest about whether a real labelled
  tender exists or not.
- Section 5 must paste ACTUAL terminal output — not
  describe what the output should look like.
- Section 6 must paste ACTUAL require_admin_key() code.
- Section 7 must paste ACTUAL SQL query output.
- Section 8 (MVP Status) must reflect ACTUAL state —
  not aspirational. If tests are missing for a REQ,
  mark as ⚠️ not ✅.
- If any AC is NOT verified: document it as
  ⚠️ NO_DATA with the reason — never mark as PASS
  without evidence.
- Output as a single markdown file:
  docs/reports/REQ-012_Implementation_Report.md

---

## After the report is generated
Run this final MVP sanity check and include output
under "Final MVP Sanity Check":

  python -c "
  # Verify complete pipeline is importable
  from app.agents.graph import graph
  from app.agents.nodes.ingestor import ingestor_node
  from app.agents.nodes.supervisor import supervisor_node
  from app.agents.nodes.risk_radar import risk_radar_node
  from app.agents.nodes.feasibility_scorer import (
      feasibility_scorer_node
  )
  from app.agents.nodes.financial_analyst import (
      financial_analyst_node
  )
  from app.agents.nodes.aggregator import (
      results_aggregator_node
  )
  from app.agents.nodes.report_assembler import (
      report_assembler_node
  )
  from app.services.event_bus import EventBus
  from eval.schemas import EvalRunResult
  from eval.run_eval import (
      run_risk_radar_eval,
      run_scorer_consistency_eval
  )

  print('✅ All nodes importable')
  print('✅ EventBus importable')
  print('✅ Eval schemas importable')
  print('✅ Eval functions importable')
  print()
  print('Graph nodes:', list(graph.get_graph().nodes.keys()))
  print()

  # Verify all skill packages
  from app.agents.skills.risk_clause_extraction import (
      RiskRadarOutput
  )
  from app.agents.skills.feasibility_scoring import (
      FeasibilityOutput, compute_go_no_go
  )
  from app.agents.skills.financial_extraction import (
      FinancialOutput
  )
  from app.agents.skills.report_synthesis import (
      ReportOutput, GoNoGo, compute_go_no_go as cgng
  )
  print('✅ All 4 skill packages importable')
  print()

  # Verify Go/No-Go thresholds
  assert cgng(70.0).value == 'GO'
  assert cgng(69.9).value == 'REVIEW'
  assert cgng(39.9).value == 'DECLINE'
  print('✅ Go/No-Go thresholds correct')
  print()
  print('🎉 TenderIQ MVP — All systems operational')
  "

  Expected final output:
    ✅ All nodes importable
    ✅ EventBus importable
    ✅ Eval schemas importable
    ✅ Eval functions importable

    Graph nodes: ['supervisor', 'risk_radar', 'scorer',
                  'financial', 'aggregator',
                  'report_assembler']

    ✅ All 4 skill packages importable

    ✅ Go/No-Go thresholds correct

    🎉 TenderIQ MVP — All systems operational