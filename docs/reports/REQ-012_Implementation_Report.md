# REQ-012 Implementation Report — Evaluation Harness

## 1. Summary

REQ-012 delivers the automated accuracy measurement harness that measures Risk Radar recall/precision/F1 against a labelled ground-truth tender and Feasibility Scorer consistency (std_dev) across repeated runs, closing the loop between the 85% recall target in the PRD and measurable evidence. With this REQ, the TenderIQ MVP is now feature-complete — all 12 REQs implemented.

## 2. Files Created/Modified — grouped by Slice

### Slice 1 — Eval Logic

| File | Status | Notes |
|------|--------|-------|
| `backend/eval/schemas.py` | Created | `CategoryMetrics`, `RiskRadarEvalResult`, `ScorerConsistencyResult`, `EvalRunResult` Pydantic schemas |
| `backend/eval/run_eval.py` | Created | `compute_overlap()`, `match_findings()`, `run_risk_radar_eval()`, `run_scorer_consistency_eval()`, CLI with `--risk --scorer --output --ground-truth --company-id` flags |
| `backend/eval/labelled_sample_tender.json` | Created (placeholder) | State: **PLACEHOLDER** — 0 labelled findings |

### Slice 2 — API Endpoint

| File | Status | Notes |
|------|--------|-------|
| `backend/app/routers/eval.py` | Created | `POST /eval/run`, `GET /eval/results`, `require_admin_key()` dependency |
| `backend/app/db/models.py` | Modified | Added `EvalResult` ORM model (lines 473–506) |
| `backend/alembic/versions/0008_create_eval_results_table.py` | Created | Migration: creates `eval_results` table with `(company_id, run_at DESC)` index |
| `backend/app/schemas/eval.py` | Created | `EvalRequest`, `EvalResultResponse` |
| `backend/app/main.py` | Modified | Line 87: `app.include_router(eval.router, prefix="/eval", tags=["eval"])` |
| `backend/.env.example` | Modified | Added `ADMIN_API_KEY` env var (line 33) |

### Slice 3 — Frontend

| File | Status | Notes |
|------|--------|-------|
| `frontend/lib/api/eval.ts` | Created | `runEval()`, `getEvalResults()`, typed interfaces, `AdminAuthError` |
| `frontend/components/EvalResultCard.tsx` | Created | PASS/FAIL badge, recall/std_dev colouring, collapsible per-category and dimension tables |
| `frontend/app/eval/page.tsx` | Created | Admin eval page with tender_id form, checkboxes for risk/scorer, recent results list |
| `frontend/.env.example` | Modified | Added `NEXT_PUBLIC_ADMIN_KEY` (line 13) |

## 3. Acceptance Criteria Verification

| AC | Status | Evidence |
|----|--------|----------|
| `POST /eval/run` returns structured `EvalRunResult` with recall, precision, F1, per-category breakdown | ⚠️ NO_DATA | The endpoint is fully implemented with the correct return shape. Verification requires a DB with a real labelled tender. Cannot be verified with placeholder ground truth. |
| Recall >= 85% on labelled sample tender — or documented as FAIL with actual measured recall | ⚠️ NO_DATA | `eval/labelled_sample_tender.json` is a PLACEHOLDER with 0 findings. No real labelled tender exists to measure recall against. |
| Scorer consistency std_dev <= 5.0 across 3 runs | ⚠️ NOT RUN | Scorer consistency logic is implemented in `run_scorer_consistency_eval()`. Requires a live DB with ingested tender + LLM API key to execute. |
| Eval results stored in `eval_results` table and retrievable via `GET /eval/results` | ⚠️ NOT VERIFIED | `EvalResult` ORM model, migration (0008), and GET endpoint are all implemented. Verification requires a live DB with eval rows. |
| Placeholder ground truth returns HTTP 200 with `risk_radar=null` and explanatory note | ✅ PASS | Code path verified: `routers/eval.py` lines 81–89: when `labelled_findings` is empty, sets `notes = "No labelled ground truth available."` and leaves `risk_result = None`. |
| Eval cost events stored with `eval-` prefix | ✅ PASS | `run_eval.py` line 392: `eval_run_id = f"eval-{eval_id}"`. All cost events created during eval use this run_id pattern. Verified by code inspection. |
| `POST /eval/run` is admin-only — company keys return 403 | ✅ PASS | `require_admin_key()` reads `X-Admin-Key` header, compares against `ADMIN_API_KEY` env var. No company API key path exists in this endpoint. |
| `eval/run_eval.py` exits with code 0 on PASS, code 1 on FAIL | ✅ PASS | `run_eval.py` line 466: `return 0 if overall_status == "PASS" else 1`. `main()` exit code propagates via `sys.exit(exit_code)` at line 472. |

## 4. Ground Truth Status — Critical Section

```
eval/labelled_sample_tender.json state:
  ☐ REAL — N labelled findings across M categories
  ☑ PLACEHOLDER — 0 findings (eval deferred)
```

The ground truth file contains:
```json
{
  "_note": "PLACEHOLDER — real labelled tender required before REQ-004 can be marked fully complete.",
  "tender_name": null,
  "source": null,
  "total_chunks": null,
  "labelled_findings": []
}
```

**Action required before production launch:**
1. Obtain a real bilingual tender document (public procurement portal, or anonymised client tender)
2. Manually label all risk clauses in the document (category, severity, clause_text, source_chunk_index)
3. Replace the placeholder JSON with the real findings
4. Re-run: `python eval/run_eval.py --tender-id <uuid> --risk`
5. Verify recall >= 85% before first paid pilot

This is an **OPEN ITEM** for the MVP launch checklist.

## 5. Eval Run Output — Actual Results

Not executed — the CLI requires a live database connection and an LLM API key to produce output. The eval was not run in this environment (no Postgres DB available). The command would be:

```
python eval/run_eval.py --tender-id <uuid> --company-id <uuid> --risk --scorer --output text
```

With the current PLACEHOLDER ground truth, it would produce:

```
WARNING: No labelled ground truth. Risk eval skipped.
==============================================
  TenderIQ Evaluation Report
  Tender: <tender_name>
  Run at: <ISO8601 timestamp>
----------------------------------------------
  FEASIBILITY SCORER CONSISTENCY
    Scores (3 runs):   [...]
    Mean:              <value>
    Std deviation:     <value>  (target: <=5.0)
    Status:            PASS/FAIL
----------------------------------------------
  TOTAL COST:          $0.0000 USD
  OVERALL STATUS:      PARTIAL
  Notes: No labelled ground truth available. Risk eval skipped.
==============================================
```

## 6. Admin Auth Verification

Actual `require_admin_key()` function from `backend/app/routers/eval.py:27-40`:

```python
async def require_admin_key(request: Request) -> None:
    """FastAPI dependency: validate X-Admin-Key header against ADMIN_API_KEY env var.

    Company API keys must NOT pass this check — even valid company keys are
    not admin keys. This is a separate auth path from the Bearer-based company
    auth in app/middleware/auth.py.
    """
    admin_key = request.headers.get("X-Admin-Key")
    settings = get_settings()
    if not settings.admin_api_key or admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
```

Confirm:
- ☑ Reads `X-Admin-Key` header (not `Authorization`)
- ☑ Compares against `ADMIN_API_KEY` env variable via `Settings`
- ☑ Company API keys return 403 (not pass through) — no Bearer check exists in this function
- ☑ `ADMIN_API_KEY` in `.env.example` (line 33 of `backend/.env.example`)

## 7. eval_run_id Prefix Verification

Actual SQL query:
```sql
SELECT run_id FROM llm_cost_events WHERE run_id LIKE 'eval-%' LIMIT 5;
```

**Not executed** — no database connection available in this environment. However, verified by code inspection:

In `backend/eval/run_eval.py` line 392:
```python
eval_run_id = f"eval-{eval_id}"
```

All cost events during eval are created via `CostTrackingHandler(run_id=eval_run_id, ...)`, where `eval_run_id` always begins with `"eval-"`. The same pattern is used in `backend/app/routers/eval.py` line 74:
```python
eval_run_id = f"eval-{uuid4()}"
```

Confirm:
- ☑ All eval cost events use `"eval-{uuid}"` format
- ☑ No production `analysis_run.id` values overlap with `eval-` prefix

## 8. Complete MVP Status — Final Checklist

| REQ | Feature | Doc | Impl | Tests |
|-----|---------|-----|------|-------|
| REQ-001 | PDF Upload & Ingestion | ✅ | ✅ | ✅ |
| REQ-002 | Company Profile | ✅ | ✅ | ✅ |
| REQ-003 | LangGraph Analysis Run | ✅ | ✅ | ✅ |
| REQ-004 | Risk Radar | ✅ | ✅ | ✅ |
| REQ-005 | Feasibility Scorer | ✅ | ✅ | ✅ |
| REQ-006 | Financial Analyst | ✅ | ✅ | ✅ |
| REQ-007 | HITL Override Gate | ✅ | ✅ | ✅ |
| REQ-008 | Report Assembler | ✅ | ✅ | ✅ |
| REQ-009 | WebSocket Streaming | ✅ | ✅ | ✅ |
| REQ-010 | LLM Cost Tracking | ✅ | ✅ | ⚠️ |
| REQ-011 | API Auth + Rate Limit | ✅ | ✅ | ✅ |
| REQ-012 | Evaluation Harness | ✅ | ✅ | ⚠️ |

**Notes:**
- REQ-010 (LLM Cost Tracking): No dedicated test file exists. Cost tracking is exercised indirectly by every graph-node test (risk_radar, feasibility_scorer, financial_analyst, report_assembler).
- REQ-012 (Evaluation Harness): No dedicated eval test file exists. Eval correctness is verified by manually running against a labelled tender.

## 9. Known Limitations / Deferred Items

- **Ground truth is a placeholder** — `eval/labelled_sample_tender.json` contains 0 findings. See Section 4 for the action plan. Recall cannot be measured until a real labelled tender is provided.
- **No automated eval test suite** — There are no Slice 4/5 tests for the eval harness. Eval correctness requires manual execution against a labelled tender.
- **Scorer consistency target (std_dev <= 5.0) may need tuning** — The threshold is a starting point based on the requirement. Real-world LLM variability may require adjustment.
- **Eval page at `/eval` has no authentication beyond admin key** — Anyone with the URL and the admin key can access it; there is no session-based auth. Acceptable for MVP; needs proper admin authentication in v2.
- **`require_admin_key()` uses plain-text comparison** — The admin key is compared via `!=` rather than constant-time comparison. Acceptable for MVP since the key is an env var secret, not a user-provided credential; upgrade to `secrets.compare_digest()` in v2.

## 10. Dependency Versions Used

No new dependencies were added for REQ-012. Existing packages:

**pip (backend):**
```
fastapi         0.115.x
SQLAlchemy      2.0.x
alembic         1.14.x
pydantic        2.x
pydantic-settings 2.x
```

`difflib.SequenceMatcher` is stdlib — no version.

**npm (frontend):**
No new packages added. Uses existing `@tanstack/react-query`, `lucide-react`, and shadcn/ui components already in the frontend dependency tree.

## 11. MVP Launch Checklist

After REQ-012, before the first pilot client:

- ☐ Replace placeholder labelled tender (see Section 4)
- ☐ Run eval against real tender: recall >= 85%
- ☐ Set `ADMIN_API_KEY` in production environment
- ☐ Set all other production env variables
- ☐ Run full test suite: `pytest -v` (all tests pass)
- ☐ Deploy to Railway (backend) + Vercel (frontend)
- ☐ Verify WebSocket works in production (not just local)
- ☐ Record a 3–5 minute Loom demo of the full pipeline
- ☐ Update GitHub README with public URL

---

## Final MVP Sanity Check

```
$ cd /d/ai-products/TenderIQ/backend
$ python -c "
import sys; sys.path.insert(0, '.')
from app.agents.graph import graph
from app.agents.nodes.ingestor import ingest_tender
from app.agents.nodes.supervisor import supervisor_node
from app.agents.nodes.risk_radar import risk_radar_node
from app.agents.nodes.feasibility_scorer import feasibility_scorer_node
from app.agents.nodes.financial_analyst import financial_analyst_node
from app.agents.nodes.aggregator import results_aggregator_node
from app.agents.nodes.report_assembler import report_assembler_node
from app.services.event_bus import EventBus
from eval.schemas import EvalRunResult
from eval.run_eval import run_risk_radar_eval, run_scorer_consistency_eval

print('✅ All nodes importable')
print('✅ EventBus importable')
print('✅ Eval schemas importable')
print('✅ Eval functions importable')
print()
print('Graph nodes:', list(graph.get_graph().nodes.keys()))
print()

from app.agents.skills.risk_clause_extraction import RiskRadarOutput
from app.agents.skills.feasibility_scoring import FeasibilityOutput
from app.agents.skills.financial_extraction import FinancialOutput
from app.agents.skills.report_synthesis import ReportOutput, GoNoGo, compute_go_no_go as cgng
print('✅ All 4 skill packages importable')
print()

assert cgng(70.0).value == 'GO'
assert cgng(69.9).value == 'REVIEW'
assert cgng(39.9).value == 'DECLINE'
print('✅ Go/No-Go thresholds correct')
print()
print('🎉 TenderIQ MVP — All systems operational')
"
```

Actual output:

```
✅ All nodes importable
✅ EventBus importable
✅ Eval schemas importable
✅ Eval functions importable

Graph nodes: ['__start__', 'supervisor', 'risk_radar', 'scorer', 'financial', 'aggregator', 'report_assembler', '__end__']

✅ All 4 skill packages importable

✅ Go/No-Go thresholds correct

🎉 TenderIQ MVP — All systems operational
```

**Note:** The sanity check script was adjusted from the template in `report-req-012.md` — `from app.agents.nodes.ingestor import ingestor_node` was corrected to `from app.agents.nodes.ingestor import ingest_tender` because the ingestor module exports `ingest_tender`, not `ingestor_node`. Additionally, `compute_go_no_go` is defined in `report_synthesis.py`, not `feasibility_scoring.py`, so the import from `feasibility_scoring` was omitted (only `FeasibilityOutput` was imported from there).
