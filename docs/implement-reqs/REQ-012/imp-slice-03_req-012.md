Read the following documents before writing any code:
- docs/reqs/REQ-012_Evaluation_Harness.md

You are implementing **REQ-012 — Slice 3 (Frontend) only**.

Slices 1 and 2 are already complete. The following is available:
- POST /eval/run (X-Admin-Key header) → EvalResultResponse
- GET /eval/results (X-Admin-Key header) → list[EvalResultResponse]

EvalResultResponse shape:
{
  id:             string (UUID)
  tender_id:      string (UUID)
  overall_status: "PASS" | "FAIL" | "PARTIAL" | "NO_DATA"
  total_cost_usd: number
  run_at:         string (ISO 8601)
  result: {
    eval_id:       string
    tender_name:   string
    risk_radar: {
      recall:         number,
      precision:      number,
      f1:             number,
      total_labelled: number,
      total_found:    number,
      total_matched:  number,
      per_category:   [{category, recall, precision,
                        labelled, found, matched}],
      pass_fail:      "PASS" | "FAIL"
    } | null,
    scorer: {
      scores:           number[],
      mean:             number,
      std_dev:          number,
      pass_fail:        "PASS" | "FAIL",
      dimension_ranges: {[dim: string]: [number, number]}
    } | null,
    total_cost_usd: number,
    overall_status: string,
    notes:          string | null
  }
}

---

## Your scope (do not touch anything outside this list)
- frontend/lib/api/eval.ts (create)
- frontend/components/EvalResultCard.tsx (create)
- frontend/app/eval/page.tsx (create)

---

## What to implement

### 1. frontend/lib/api/eval.ts

  type CategoryMetrics = {
    category:   string
    recall:     number
    precision:  number
    labelled:   number
    found:      number
    matched:    number
  }

  type RiskRadarEvalResult = {
    recall:         number
    precision:      number
    f1:             number
    total_labelled: number
    total_found:    number
    total_matched:  number
    per_category:   CategoryMetrics[]
    pass_fail:      'PASS' | 'FAIL'
  }

  type ScorerConsistencyResult = {
    scores:           number[]
    mean:             number
    std_dev:          number
    pass_fail:        'PASS' | 'FAIL'
    dimension_ranges: Record<string, [number, number]>
  }

  type EvalResultResponse = {
    id:             string
    tender_id:      string
    overall_status: 'PASS' | 'FAIL' | 'PARTIAL' | 'NO_DATA'
    total_cost_usd: number
    run_at:         string
    result: {
      eval_id:       string
      tender_name:   string
      risk_radar:    RiskRadarEvalResult | null
      scorer:        ScorerConsistencyResult | null
      notes:         string | null
    }
  }

  type RunEvalRequest = {
    tender_id:              string
    run_risk_radar:         boolean
    run_scorer_consistency: boolean
  }

  const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_KEY ?? ''

  runEval(request: RunEvalRequest): Promise<EvalResultResponse>
    - POST /eval/run
    - Header: X-Admin-Key: {ADMIN_KEY}
    - On 403: throw AdminAuthError("Invalid admin key")
    - On 409: throw ConflictError(server message)
    - On 422: throw ValidationError(server message)
    - On other errors: throw ApiError with status

  getEvalResults(limit?: number): Promise<EvalResultResponse[]>
    - GET /eval/results?limit={limit ?? 10}
    - Header: X-Admin-Key: {ADMIN_KEY}
    - On 403: throw AdminAuthError
    - On other errors: throw ApiError

  Add NEXT_PUBLIC_ADMIN_KEY to .env.example:
    NEXT_PUBLIC_ADMIN_KEY=

### 2. frontend/components/EvalResultCard.tsx

Props: { result: EvalResultResponse }

A card component showing one eval result.

Overall status badge (top of card):
  PASS:    green badge (#D1FAE5 / #065F46) "✅ PASS"
  FAIL:    red badge (#FEE2E2 / #B91C1C) "❌ FAIL"
  PARTIAL: amber badge (#FEF3C7 / #92400E) "⚠ PARTIAL"
  NO_DATA: grey badge (#F3F4F6 / #374151) "— NO DATA"

Card header:
  Tender: {result.result.tender_name || result.tender_id}
  Run at: {formatted date from result.run_at}
  Cost:   ${result.total_cost_usd.toFixed(4)} USD

Risk Radar section (only if result.result.risk_radar):
  Title: "Risk Radar Accuracy"
  Status badge: PASS/FAIL per pass_fail field
  Metrics row:
    Recall:    {(recall * 100).toFixed(1)}%
               (show amber if < 85%, green if >= 85%)
    Precision: {(precision * 100).toFixed(1)}%
    F1:        {(f1 * 100).toFixed(1)}%
  Small text: "{total_matched} of {total_labelled}
               labelled clauses found
               ({total_found} total model findings)"

  Per-category table (collapsible — collapsed by default):
    Toggle: "Show category breakdown ▾"
    Columns: Category | Recall | Matched/Labelled
    Category display (human-readable):
      fidic        → "FIDIC Clauses"
      penalty      → "Penalty Clauses"
      lg_bond      → "LG / Bond"
      termination  → "Termination"
      other        → "Other"
    Recall cell: coloured amber if < 85%, green if >= 85%

Scorer Consistency section (only if result.result.scorer):
  Title: "Feasibility Scorer Consistency"
  Status badge: PASS/FAIL per pass_fail field
  Metrics row:
    Scores: [{scores joined with ", "}]
    Mean:   {mean.toFixed(1)}
    Std Dev: {std_dev.toFixed(2)}
             (amber if > 5.0, green if <= 5.0)
  Small text: "Target: std deviation ≤ 5.0 points"

  Dimension ranges table (collapsible):
    Toggle: "Show dimension ranges ▾"
    Columns: Dimension | Min Score | Max Score | Range
    Range = max - min (highlight amber if range > 5)

Notes section (only if result.result.notes is not null):
  Amber info banner with the notes text.

### 3. frontend/app/eval/page.tsx

  A minimal admin page — not linked from main nav.
  Access via /eval URL directly.

  Warning banner at top (always visible):
    "🔒 Admin-only page. Requires NEXT_PUBLIC_ADMIN_KEY
     to be set in environment variables."

  Run Eval form:
    Input: Tender ID (UUID text input, required)
    Checkboxes:
      ☑ Run Risk Radar Accuracy Eval (default: checked)
      ☐ Run Scorer Consistency Eval (default: unchecked)
    Button: "Run Evaluation"
    Loading state: "Running evaluation... this may take
                   up to 3 minutes." with spinner

    On success: show the new EvalResultCard at top of
    results list, scroll to it.
    On AdminAuthError: show red banner:
      "Admin key not configured. Set NEXT_PUBLIC_ADMIN_KEY
       in .env.local"
    On other errors: show red banner with error message.

  Recent Results section:
    Title: "Recent Evaluations (last 10)"
    useQuery to fetch GET /eval/results on mount
    Show list of EvalResultCard components
    If empty: "No evaluations run yet."
    Show skeleton while loading

  Data fetching:
    useQuery({
      queryKey: ['eval-results'],
      queryFn: () => getEvalResults(10),
    })
    TanStack Query v5 syntax.
    After successful runEval():
      queryClient.invalidateQueries({
        queryKey: ['eval-results']
      })
      This refreshes the results list automatically.

---

## Rules
- Do NOT modify any backend files.
- Do NOT add this page to the main navigation —
  it is an admin-only page accessed directly via URL.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax throughout.
- Use Shadcn/ui for all UI components.
- TypeScript strict mode — no `any` types.
- NEXT_PUBLIC_ADMIN_KEY must come from environment —
  never hardcoded. Add to .env.example.
- Recall percentage colouring threshold:
  < 85% → amber, >= 85% → green.
  This is the PRD target — use it consistently.
- Std dev colouring threshold:
  > 5.0 → amber, <= 5.0 → green.
- Per-category and dimension-ranges tables must be
  collapsible — collapsed by default to keep the card
  compact when showing multiple results.
- The page must work even if ADMIN_KEY is empty —
  show AdminAuthError banner on first API call.

---

## When you finish
Show me:
1. Full file tree created (3 files)
2. Confirm TanStack Query v5 useQuery and
   invalidateQueries syntax — show both calls
3. Confirm recall colouring < 85% amber, >= 85% green —
   show the conditional colour logic
4. Confirm per-category table is collapsible —
   show the useState toggle implementation
5. Open /eval in browser and show me:
   - Warning banner visible
   - Run form with tender ID input and checkboxes
   - After running: EvalResultCard appears at top
   - Overall PASS/FAIL badge correct colour
   - Recall percentage coloured correctly
   - "Show category breakdown" toggle works
   - Recent results list loads on mount

REQ-012 is only complete once all 3 slices pass review.
After REQ-012, TenderIQ MVP documentation and
implementation are feature-complete.
Do not declare MVP complete until I explicitly say so.