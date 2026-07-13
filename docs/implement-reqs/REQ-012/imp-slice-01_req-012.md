Read the following documents before writing any code:
- docs/reqs/REQ-012_Evaluation_Harness.md
- docs/reqs/REQ-004_Risk_Radar_Node.md (section 5,
  Slice 5 — eval/run_eval.py and labelled ground truth)

You are implementing **REQ-012 — Slice 1 (Eval Logic) only**.

The following already exists from REQ-004 Slice 5:
- eval/run_eval.py → basic script that loads
  labelled_sample_tender.json and computes recall
  against a single risk_radar run. May be a stub
  or partial implementation.
- eval/labelled_sample_tender.json → either a real
  labelled tender or a placeholder with empty findings.

---

## Your scope (do not touch anything outside this list)
- eval/run_eval.py (extend — do not rewrite from scratch)
- eval/labelled_sample_tender.json (finalise if placeholder
  — instructions below)
- eval/schemas.py (create — shared Pydantic schemas for
  eval results, used by both CLI and API endpoint)

---

## What to implement

### 1. eval/schemas.py — Eval result schemas

  from pydantic import BaseModel
  from typing import Literal

  class CategoryMetrics(BaseModel):
      category:   str
      recall:     float
      precision:  float
      labelled:   int
      found:      int
      matched:    int

  class RiskRadarEvalResult(BaseModel):
      recall:         float
      precision:      float
      f1:             float
      total_labelled: int
      total_found:    int
      total_matched:  int
      per_category:   list[CategoryMetrics]
      pass_fail:      str  # "PASS" if recall >= 0.85

  class ScorerConsistencyResult(BaseModel):
      scores:           list[float]  # 3 runs
      mean:             float
      std_dev:          float
      pass_fail:        str  # "PASS" if std_dev <= 5.0
      dimension_ranges: dict  # {dim_name: [min, max]}

  class EvalRunResult(BaseModel):
      eval_id:        str
      tender_id:      str
      tender_name:    str
      run_at:         str
      risk_radar:     RiskRadarEvalResult | None
      scorer:         ScorerConsistencyResult | None
      total_cost_usd: float
      overall_status: str  # "PASS"|"FAIL"|"PARTIAL"|"NO_DATA"
      notes:          str | None

### 2. Matching function — in eval/run_eval.py

  def compute_overlap(text_a: str, text_b: str) -> float:
      """
      Compute substring overlap between two texts.
      Returns 0.0-1.0. Consistent with REQ-004's 0.70 threshold.
      Use SequenceMatcher from difflib — no external deps.
      """
      from difflib import SequenceMatcher
      return SequenceMatcher(
          None,
          text_a.lower().strip(),
          text_b.lower().strip()
      ).ratio()

  def match_findings(
      model_findings: list[dict],
      labelled_findings: list[dict],
      threshold: float = 0.70,
  ) -> tuple[int, list[dict], list[dict]]:
      """
      Returns (matched_count, matched_pairs, unmatched_labelled).
      Uses greedy matching: each labelled finding can only be
      matched to one model finding.
      """

### 3. Risk Radar eval function — in eval/run_eval.py

  async def run_risk_radar_eval(
      tender_id: str,
      labelled_findings: list[dict],
      eval_run_id: str,
  ) -> RiskRadarEvalResult:
      """
      Runs risk_radar_node against real tender chunks from DB.
      Uses eval_run_id prefixed "eval-" for cost tracking.
      """

  Steps:
  a) Fetch tender_chunks from DB for this tender_id
     (use AsyncSession, same pattern as production code)
  b) Build a minimal TenderState:
     state = {
       "tender_id": tender_id,
       "run_id": eval_run_id,   # "eval-{uuid}"
       "company_id": "eval",
       "chunks": [chunk dicts from DB],
       "supervisor_ready": True,
       "risk_findings": [],
       "feasibility_score": None,
       "feasibility_breakdown": None,
       "financial_summary": {},
       "aggregated_results": None,
       "hitl_approved": False,
       "hitl_override_score": None,
       "final_report": None,
       "token_usage": [],
       "source_languages": [],
     }
  c) Call risk_radar_node(state, {})
  d) model_findings = result["risk_findings"]
  e) Compute overall metrics using match_findings()
  f) Compute per-category metrics:
     For each category in [fidic, penalty, lg_bond,
                           termination, other]:
       labelled_in_cat = [f for f in labelled_findings
                          if f["category"] == cat]
       found_in_cat = [f for f in model_findings
                       if f["category"] == cat]
       matched_in_cat, _, _ = match_findings(
           found_in_cat, labelled_in_cat
       )
  g) Compute recall, precision, F1
  h) Return RiskRadarEvalResult

### 4. Scorer consistency function — in eval/run_eval.py

  async def run_scorer_consistency_eval(
      tender_id: str,
      company_id: str,
      eval_run_id: str,
  ) -> ScorerConsistencyResult:
      """
      Runs feasibility_scorer_node 3 times on the same
      tender+company and measures score consistency.
      """

  Steps:
  a) Fetch tender_chunks from DB for this tender_id
  b) For i in range(3):
       Build TenderState with run_id=f"{eval_run_id}-{i}"
       Set supervisor_ready=True (skip supervisor)
       Call feasibility_scorer_node(state, {})
       scores.append(result["feasibility_score"])
       breakdown_list.append(result["feasibility_breakdown"])
  c) Compute mean = sum(scores) / 3
  d) Compute std_dev = statistics.stdev(scores)
  e) Compute dimension_ranges:
     For each dimension in feasibility_breakdown keys:
       dim_scores = [b[dim]["score"] for b in breakdown_list
                     if b and dim in b]
       dimension_ranges[dim] = [min(dim_scores), max(dim_scores)]
  f) Return ScorerConsistencyResult

### 5. Main CLI function — extend eval/run_eval.py

  Extend the existing argparse-based CLI to support:

  python eval/run_eval.py \
    --tender-id <uuid> \
    --company-id <uuid>  \  # needed for scorer eval
    --risk \               # run risk radar eval
    --scorer \             # run scorer consistency eval
    --output json|text     # default: text

  Behaviour:
  a) Load eval/labelled_sample_tender.json
  b) If labelled_findings is empty AND --risk flag set:
     Print: "⚠ No labelled ground truth. Risk eval skipped."
     Set risk_radar = None
  c) Run selected evals
  d) Compute overall_status:
     All enabled evals pass → "PASS"
     Any enabled eval fails → "FAIL"
     Some skipped (no data) → "PARTIAL"
  e) If --output json: print EvalRunResult.model_dump_json()
  f) If --output text: print formatted report (see format below)
  g) Exit code 0 if overall_status="PASS", 1 otherwise

  Text output format:
  ════════════════════════════════════════
  TenderIQ Evaluation Report
  Tender: {tender_name}
  Run at: {run_at}
  ────────────────────────────────────────
  RISK RADAR
    Labelled clauses:  {total_labelled}
    Model findings:    {total_found}
    Matched:           {total_matched}
    Recall:            {recall:.1%}  (target: ≥85%)
    Precision:         {precision:.1%}
    F1:                {f1:.1%}
    Status:            ✅ PASS / ❌ FAIL

    Per category:
      fidic:       {recall:.1%} ({matched}/{labelled})
      penalty:     ...
      lg_bond:     ...
      termination: ...
      other:       ...
  ────────────────────────────────────────
  FEASIBILITY SCORER CONSISTENCY
    Scores (3 runs):   [{s1:.1f}, {s2:.1f}, {s3:.1f}]
    Mean:              {mean:.1f}
    Std deviation:     {std_dev:.1f}  (target: ≤5.0)
    Status:            ✅ PASS / ❌ FAIL
  ────────────────────────────────────────
  TOTAL COST:          ${total_cost_usd:.4f} USD
  OVERALL STATUS:      ✅ PASS / ❌ FAIL / ⚠ PARTIAL
  ════════════════════════════════════════

### 6. eval/labelled_sample_tender.json — finalise
  Check the current state of this file from REQ-004:
  - If it is a real labelled tender with findings: keep it.
  - If it is a placeholder (empty findings): you MUST
    flag this explicitly in your summary.
    Do NOT fabricate fake labelled findings —
    write: "PLACEHOLDER — real labelled tender required"
    and leave findings as [].
  The eval will run correctly with a placeholder —
  it will just return risk_radar=null and note="No data".

---

## Rules
- Do NOT import from app/ inside eval/run_eval.py using
  relative imports — use absolute imports with the project
  root in sys.path, or use direct DB connection strings.
- Do NOT modify any node files (risk_radar.py,
  feasibility_scorer.py) — call them as-is.
- Do NOT create API endpoint or frontend files — Slice 2.
- eval_run_id for cost tracking must always start with
  "eval-" to distinguish from production run IDs.
- compute_overlap() must use difflib.SequenceMatcher
  — no external NLP libraries.
- The scorer consistency eval must run exactly 3 times
  — not configurable for MVP.
- Never modify eval/labelled_sample_tender.json during
  an eval run — it is read-only ground truth.
- statistics.stdev() requires at least 2 values —
  handle the edge case where all 3 scores are identical
  (stdev returns 0.0, which is a valid PASS result).

---

## When you finish
Show me:
1. Full contents of eval/schemas.py
2. Full contents of eval/run_eval.py (extended)
3. State of eval/labelled_sample_tender.json:
   "REAL — {N} labelled findings" or "PLACEHOLDER — 0 findings"
4. Run the CLI with --output text and show actual output:
   python eval/run_eval.py \
     --tender-id <a_real_ready_tender_uuid> \
     --company-id <company_uuid> \
     --risk --scorer \
     --output text
   Show me the ACTUAL terminal output.
   If placeholder: show the "no data" output.
5. Run with --output json and show first 20 lines:
   python eval/run_eval.py \
     --tender-id <uuid> --risk \
     --output json | head -20
6. Confirm exit code behaviour:
   python eval/run_eval.py --tender-id <uuid> --risk
   echo "Exit code: $?"
   Show actual exit code.

Do not move to Slice 2 until I explicitly tell you to.