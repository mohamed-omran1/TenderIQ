Read the following documents before writing any code:
- docs/reqs/REQ-006_Financial_Analyst_Node.md

You are implementing **REQ-006 — Slice 3 (Persistence) only**.

Slices 1 and 2 are already complete. The following is available:
- financial_analyst_node returns:
  {
    "financial_summary": {
      "contract_value":     {"value": float, "currency": str,
                             "needs_review": bool} | null,
      "bonds":              [{"bond_type": str, "amount": {...},
                              "percentage": float|null,
                              "conditions": str,
                              "source_chunk_index": int}, ...],
      "liquidated_damages": {"rate": {...}, "period": str,
                             "cap": {...}|null,
                             "cap_percentage": float|null,
                             "source_chunk_index": int} | null,
      "payment_schedule":   [{"description": str,
                               "percentage": float|null,
                               "amount": {...}|null,
                               "trigger": str}, ...],
      "retention_rate":     float | null,
      "advance_payment":    {"value": float, "currency": str,
                             "needs_review": bool} | null,
    }
  }

The current atomic commit block in routers/tenders.py
after REQ-004 and REQ-005 covers:
  1. INSERT into risk_findings (REQ-004)
  2. UPDATE analysis_runs SET feasibility_score (REQ-005)
  3. UPDATE analysis_runs SET state = "awaiting_hitl"
  All in ONE db.commit()

---

## Your scope (do not touch anything outside this list)
- app/db/models.py (add FinancialCommitment ORM model only)
- alembic/versions/xxxx_create_financial_commitments_table.py
- app/api/routers/tenders.py (extend atomic commit block only)
- app/schemas/analysis.py (add FinancialCommitmentResponse)

---

## What to implement

### 1. Alembic migration — financial_commitments table
Columns:
  id:                UUID PK, server default gen_random_uuid()
  run_id:            UUID FK → analysis_runs.id, not null
  commitment_type:   VARCHAR not null
                     values: bond | liquidated_damages |
                     payment_milestone | retention |
                     advance_payment | contract_value
  amount_value:      FLOAT nullable
  amount_currency:   VARCHAR(10) nullable
  percentage:        FLOAT nullable
  description:       TEXT not null
  needs_review:      BOOLEAN not null default false
  source_chunk_index: INTEGER nullable

Indexes:
  - CREATE INDEX on (run_id)
  - CREATE INDEX on (run_id, commitment_type)
  - CREATE INDEX on (run_id, needs_review) — fast query for
    items flagged for analyst review

### 2. SQLAlchemy ORM model — FinancialCommitment
  class FinancialCommitment(Base):
      __tablename__ = "financial_commitments"
      id:                 UUID PK
      run_id:             UUID FK → analysis_runs.id
      commitment_type:    str
      amount_value:       float | None
      amount_currency:    str | None
      percentage:         float | None
      description:        str
      needs_review:       bool
      source_chunk_index: int | None

  Relationship: FinancialCommitment.run → AnalysisRun (many-to-one)
  Back-reference: AnalysisRun.financial_commitments → list[FinancialCommitment]

  Name conflict note: import as FinancialCommitmentModel where
  needed to avoid confusion with any Pydantic schema.

### 3. Extend the atomic commit block in run_graph()
Find the existing block. It currently looks like this after
REQ-004 and REQ-005:

  final_checkpoint = await graph.aget_state(config)

  # REQ-004: risk findings
  findings_dicts = final_checkpoint.values.get("risk_findings", [])
  if findings_dicts:
      await db.execute(insert(RiskFindingModel).values([...]))

  # REQ-005: feasibility score + state transition
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

  await db.commit()  # ← single commit covers operations 1-3

Extend to add financial_commitments INSERT before the commit:

  # REQ-006: financial commitments
  financial_summary = final_checkpoint.values.get(
      "financial_summary", {}
  )

  # Do NOT persist if error key present (degraded path)
  if "error" not in financial_summary:
      commitment_rows = _flatten_financial_summary(
          financial_summary, run_id
      )
      if commitment_rows:
          await db.execute(
              insert(FinancialCommitmentModel).values(commitment_rows)
          )

  # Single commit still — now covers all four operations
  await db.commit()

Implement _flatten_financial_summary() as a private helper
in routers/tenders.py:

  def _flatten_financial_summary(
      summary: dict, run_id: UUID
  ) -> list[dict]:
      """
      Converts the nested financial_summary dict into a flat
      list of dicts ready for bulk INSERT into financial_commitments.
      One row per commitment item across all categories.
      """

  Mapping:
    contract_value → commitment_type="contract_value",
      amount_value=summary["contract_value"]["value"],
      amount_currency=summary["contract_value"]["currency"],
      needs_review=summary["contract_value"]["needs_review"],
      description="Contract value",
      source_chunk_index=None

    Each bond in bonds → commitment_type="bond",
      amount_value=bond["amount"]["value"],
      amount_currency=bond["amount"]["currency"],
      percentage=bond.get("percentage"),
      description=bond["conditions"],
      needs_review=bond["amount"]["needs_review"],
      source_chunk_index=bond["source_chunk_index"]

    liquidated_damages → commitment_type="liquidated_damages",
      amount_value=ld["rate"]["value"],
      amount_currency=ld["rate"]["currency"],
      percentage=ld.get("cap_percentage"),
      description=f"LD rate: {ld['period']}. "
                  f"Cap: {ld['cap']['value'] if ld.get('cap') else 'None'}",
      needs_review=ld["rate"]["needs_review"],
      source_chunk_index=ld["source_chunk_index"]

    Each milestone in payment_schedule →
      commitment_type="payment_milestone",
      amount_value=milestone["amount"]["value"]
        if milestone.get("amount") else None,
      amount_currency=milestone["amount"]["currency"]
        if milestone.get("amount") else None,
      percentage=milestone.get("percentage"),
      description=f"{milestone['description']} — {milestone['trigger']}",
      needs_review=milestone["amount"]["needs_review"]
        if milestone.get("amount") else False,
      source_chunk_index=None

    retention_rate → commitment_type="retention",
      amount_value=None,
      amount_currency=None,
      percentage=summary["retention_rate"],
      description=f"Retention: {summary['retention_rate']}% of contract value",
      needs_review=False,
      source_chunk_index=None

    advance_payment → commitment_type="advance_payment",
      amount_value=summary["advance_payment"]["value"],
      amount_currency=summary["advance_payment"]["currency"],
      needs_review=summary["advance_payment"]["needs_review"],
      description="Advance payment / mobilisation",
      source_chunk_index=None

  Always include run_id in every row dict.
  Skip any item where the source field is None/null
  (e.g. if contract_value is None, skip it).
  Never raise — wrap in try/except and return [] on any
  unexpected error (log the error with run_id, no values).

### 4. Add GET /tenders/{id}/financial endpoint
In app/api/routers/tenders.py:

  GET /tenders/{tender_id}/financial
  Auth: API key (company_id scoped)

  Logic:
    a) Resolve company_id from API key
    b) Fetch latest analysis_run for this tender_id
    c) Authorisation: run.company_id must match authenticated
    d) If state != "awaiting_hitl" and state != "complete":
       HTTP 404 "Financial summary not yet available."
    e) Query financial_commitments WHERE run_id = run.id
       ORDER BY commitment_type ASC, id ASC
    f) Return list[FinancialCommitmentResponse]

  FinancialCommitmentResponse (add to app/schemas/analysis.py):
    id:                UUID
    commitment_type:   str
    amount_value:      float | None
    amount_currency:   str | None
    percentage:        float | None
    description:       str
    needs_review:      bool
    source_chunk_index: int | None

---

## Rules
- Do NOT modify agents/nodes/financial_analyst.py.
- Do NOT modify agents/state.py or agents/graph.py.
- Do NOT create any frontend or test files.
- The atomic commit block must still have EXACTLY ONE
  db.commit() call after this change — covering all four
  operations (risk_findings, feasibility_score, financial_
  commitments, state transition). Never split into two commits.
- If financial_summary contains an "error" key (degraded path
  from Slice 2), do NOT insert any financial_commitments rows
  — skip silently. The error will be visible in the aggregated
  results but should not produce partial DB rows.
- _flatten_financial_summary() must be a pure synchronous
  function — no async, no DB calls, no I/O. It only transforms
  a dict into a list of dicts.
- Never log amount_value or amount_currency — only log
  run_id and counts at INFO level.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (4 files)
2. Run the migration:
   alembic upgrade head
   Confirm financial_commitments table created with all indexes
3. Show me the COMPLETE atomic commit block as it now stands —
   all four operations visible, one db.commit() at the end
4. Run end-to-end test:
   - Upload sample tender
   - Trigger analysis, wait for awaiting_hitl
   - Query DB directly:
     SELECT commitment_type, amount_currency,
            needs_review, description
     FROM financial_commitments
     WHERE run_id = '<run_id>'
     ORDER BY commitment_type;
   Show me actual query output
5. Call GET /tenders/{id}/financial and show me response body
6. Confirm _flatten_financial_summary() skips error path:
   python -c "
   from app.api.routers.tenders import _flatten_financial_summary
   import uuid
   error_summary = {'error': 'malformed', 'bonds': [],
                    'commitments': [], 'payment_schedule': None}
   result = _flatten_financial_summary(error_summary, uuid.uuid4())
   print('Rows from error summary:', len(result))
   # Expected: 0
   "

Do not move to Slice 4 until I explicitly tell you to.