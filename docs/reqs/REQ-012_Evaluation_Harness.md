# REQ-012
## Evaluation Harness — Automated Accuracy Measurement

### Status
| READY FOR IMPLEMENTATION

### Sprint
| Week 4 — Polish + Eval

### Priority
| P1 — Final MVP REQ. Completes the accuracy measurement loop started in REQ-004 (85% recall target).

### Dependencies
| REQ-004 complete (risk_findings in DB). REQ-005 complete (feasibility_score in DB). eval/labelled_sample_tender.json exists (even as placeholder from REQ-004 Slice 5).

### Related Docs
| TenderIQ_PRD_v1.0 §3.2 (Success Metrics — ≥85% recall target)  |  REQ-004 §5 (Slice 5 — labelled ground truth)

### Owning Component
FastAPI Eval Endpoint  |  Eval Runner Script  |  Eval Results UI

### app/api/routers/eval.py
| eval/run_eval.py (extend from REQ-004)  |  frontend/app/eval/page.tsx

---

### Description
Implement the automated accuracy evaluation harness referenced in the PRD §3.2 success metrics. The harness runs the Risk Radar node (REQ-004) against a labelled ground-truth tender and measures recall and precision against manually identified risk clauses. It also measures feasibility scoring consistency across repeated runs on the same tender.

This REQ formalises what was started informally in REQ-004 Slice 5 (eval/run_eval.py and eval/labelled_sample_tender.json). The goal is a repeatable, automated measurement that can be run after any prompt change to detect accuracy regressions before they reach production.

Two interfaces are provided: a CLI script (eval/run_eval.py, extended from REQ-004) for developer use, and a POST /eval/run API endpoint for admin use. The frontend provides a simple eval trigger page for non-developer team members.

### Preconditions
* REQ-004 complete: risk_radar_node produces real risk_findings. risk_findings table exists.
* REQ-005 complete: feasibility_scorer_node produces real feasibility_score and feasibility_breakdown.
* eval/labelled_sample_tender.json exists — even as a placeholder from REQ-004 Slice 5. If it is a placeholder, eval runs return an explicit "no labelled data" result rather than fake metrics.
* A labelled tender must be uploaded and ingested (REQ-001) before running eval — the eval harness uses real pgvector chunks, not synthetic data.

### What is Measured
#### 1. Risk Radar Recall and Precision
The primary metric from PRD §3.2. For each labelled risk clause in the ground truth:
* A model finding MATCHES a labelled clause if the clause_text of the finding has >= 0.70 substring overlap with the labelled clause_text (consistent with eval/run_eval.py from REQ-004).
* **Recall** = matched labelled clauses / total labelled clauses. Target: >= 85%.
* **Precision** = matched labelled clauses / total model findings. Secondary metric — high precision preferred but not at the cost of recall.
* **F1** = harmonic mean of recall and precision.
* **Per-category breakdown**: recall computed separately for fidic, penalty, lg_bond, termination, other.

#### 2. Feasibility Scoring Consistency
Because feasibility scoring is non-deterministic (LLM output varies), the harness measures consistency rather than accuracy:
* Run the feasibility_scorer_node 3 times on the same tender with the same company profile.
* Compute the standard deviation of the 3 composite scores.
* **Target**: standard deviation <= 5.0 points (scores should not vary by more than 5 points across runs on the same input).
* Also report the per-dimension score range across the 3 runs.

#### 3. Cost per Evaluation Run
* Report the total LLM cost (USD) of running the full eval — useful for understanding the cost of running evals as part of a CI/CD pipeline.

### Eval Result Schema
```python
class CategoryMetrics(BaseModel):
    category:   str
    recall:     float
    precision:  float
    labelled:   int   # ground truth count
    found:      int   # model findings count
    matched:    int   # overlap >= 0.70

class RiskRadarEvalResult(BaseModel):
    recall:          float   # overall
    precision:       float   # overall
    f1:              float
    total_labelled:  int
    total_found:     int
    total_matched:   int
    per_category:    list[CategoryMetrics]
    pass_fail:       str   # "PASS" if recall >= 0.85

class ScorerConsistencyResult(BaseModel):
    scores:          list[float]  # 3 runs
    mean:            float
    std_dev:         float
    pass_fail:       str  # "PASS" if std_dev <= 5.0
    dimension_ranges: dict  # {dim: (min, max)}

class EvalRunResult(BaseModel):
    eval_id:         str   # UUID
    tender_id:       str
    tender_name:     str
    run_at:          str   # ISO 8601
    risk_radar:      RiskRadarEvalResult | None
    scorer:          ScorerConsistencyResult | None
    total_cost_usd:  float
    overall_status:  str   # "PASS" | "FAIL" | "PARTIAL"
    notes:           str | None
```

### Main Flow — POST /eval/run
1. Admin calls POST /eval/run with body: `{ tender_id, run_risk_radar: bool, run_scorer_consistency: bool }`.
2. Server validates: the tender must be in "ready" state (ingested). The company_id from API key must own the tender.
3. Server loads eval/labelled_sample_tender.json. If labelled_findings is empty (placeholder): return HTTP 200 with a result where risk_radar = null and a note: `"No labelled ground truth available. Upload a labelled tender to run accuracy eval."`
4. If run_risk_radar=True: run risk_radar_node once against the tender chunks and compare findings to labelled_findings using the 0.70 overlap threshold. Compute recall, precision, F1, per-category breakdown.
5. If run_scorer_consistency=True: run feasibility_scorer_node 3 times with the same company profile. Compute mean, std_dev, dimension_ranges.
6. Compute total_cost_usd from llm_cost_events rows created during this eval run.
7. Determine overall_status: "PASS" if all enabled metrics pass their targets, "FAIL" if any fail, "PARTIAL" if some were skipped.
8. Store result in eval_results table and return HTTP 200 with EvalRunResult.

### Alternative Flows
| Condition | System Response | Resulting State |
| :--- | :--- | :--- |
| labelled_sample_tender.json is a placeholder (empty findings) | Return HTTP 200 with risk_radar=null and explanatory note. Do not run the LLM. | Partial result stored. overall_status="PARTIAL". |
| Tender not in "ready" state | HTTP 409 — "Tender must be ingested before running eval." | No eval run created. |
| LLM API failure during eval | Mark eval run as failed with error_reason. Partial results (if any) preserved. | eval_results row with status="failed". |
| Both run_risk_radar and run_scorer_consistency are False | HTTP 422 — "At least one eval type must be enabled." | No eval run created. |

### Data Requirements
#### eval_results table
| Column | Type | Notes |
| :--- | :--- | :--- |
| id | UUID PK | server default gen_random_uuid() |
| company_id | UUID FK | → companies.id. Scoped per tenant. |
| tender_id | UUID FK | → tenders.id. The tender used for eval. |
| result | JSONB | Full EvalRunResult serialised to JSON. |
| overall_status | VARCHAR | "PASS" \| "FAIL" \| "PARTIAL" \| "failed" |
| total_cost_usd | FLOAT | LLM cost of this eval run. |
| run_at | TIMESTAMP TZ | server default now() |

### Non-Functional Requirements
* **Performance**: A full eval run (risk radar + scorer consistency × 3) should complete within 3 minutes — this is a developer/admin tool, not a user-facing feature. Latency is acceptable.
* **Reproducibility**: Eval results must be stored and queryable via GET /eval/results — developers must be able to compare results across prompt versions over time.
* The labelled ground truth (eval/labelled_sample_tender.json) is the single source of truth for accuracy measurement — it must not be modified by the eval run itself.
* **Safety**: Eval runs must never affect production analysis_runs or risk_findings tables. All LLM calls during eval use a separate run_id prefixed with "eval-" to distinguish eval cost events from production cost events.
* POST /eval/run is an admin-only endpoint — requires a special ADMIN_API_KEY header separate from the company API key.

### Implementation Slices
| Slice | Owns | Scope |
| :--- | :--- | :--- |
| 1. Eval Logic | eval/run_eval.py (extend), eval/labelled_sample_tender.json (finalise if placeholder) | Extend the REQ-004 eval script to: run risk_radar_node directly against tender chunks, compute per-category metrics, run feasibility_scorer_node 3 times and compute std_dev, output EvalRunResult as JSON. CLI: python eval/run_eval.py --tender-id <uuid> --risk --scorer. |
| 2. API Endpoint | app/api/routers/eval.py, db/models.py (EvalResult ORM), alembic migration, schemas/eval.py | POST /eval/run (admin auth via ADMIN_API_KEY header). GET /eval/results (returns last 10 eval runs for this company). eval_results table + migration. EvalRunResult, EvalRequest Pydantic schemas. |
| 3. Frontend | frontend/app/eval/page.tsx, components/EvalResultCard.tsx, lib/api/eval.ts | Simple admin eval page: tender_id input, checkboxes for risk/scorer, "Run Eval" button. Shows last 10 eval results as cards with PASS/FAIL badge, recall %, std_dev, cost. Not linked from main nav — accessed directly via /eval URL. |

### Slice Activation Rule
The project owner selects which slice is executed and when. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope. The agent must not expand scope or select the next slice on its own.

### Acceptance Criteria / Definition of Done
* [ ] POST /eval/run with a real labelled tender returns a structured EvalRunResult with recall, precision, F1, and per-category breakdown.
* [ ] Recall >= 85% on the labelled sample tender — or explicitly documented as FAIL with the actual measured recall if below threshold.
* [ ] Feasibility scorer consistency: std_dev <= 5.0 across 3 runs on the same tender — or explicitly documented as FAIL with actual std_dev.
* [ ] Eval results are stored in eval_results table and retrievable via GET /eval/results.
* [ ] Eval runs with placeholder ground truth (empty labelled_findings) return HTTP 200 with risk_radar=null and an explanatory note — never fake metrics.
* [ ] Eval cost events are stored with run_id prefixed "eval-" — never mixed with production analysis_runs cost events.
* [ ] POST /eval/run is admin-only — company API keys return HTTP 403.
* [ ] Frontend eval page shows last 10 eval results with PASS/FAIL status and key metrics.
* [ ] eval/run_eval.py CLI exits with code 0 on PASS, code 1 on FAIL — suitable for CI/CD integration.

### Document Control
REQ-012 is the final REQ in the TenderIQ MVP. After this REQ is complete, the product has a full feature-complete pipeline with automated accuracy measurement. Version 2 planning begins after the first pilot client engagement.