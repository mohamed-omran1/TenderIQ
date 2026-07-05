Read the following documents before writing any code:
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md
- docs/01_PRD.md(section 8 — Frontend pages)

You are implementing **REQ-005 — Slice 4 (Frontend) only**.

Slices 1, 2, and 3 are already complete. The following is available:
- GET /tenders/{id}/status → now includes feasibility_score: float | None
- GET /tenders/{id}/findings → list[RiskFindingResponse] (REQ-004)
- app/tenders/[id]/report/page.tsx → exists from REQ-004 Slice 4,
  currently renders <RiskRadarTable /> and placeholder sections
- The feasibility_breakdown shape returned in aggregated_results:
  {
    "technical_fit":      {"score": int, "rationale": str},
    "financial_capacity": {"score": int, "rationale": str},
    "timeline":           {"score": int, "rationale": str},
    "geographic_scope":   {"score": int, "rationale": str},
    "past_experience":    {"score": int, "rationale": str},
  }

---

## Your scope (do not touch anything outside this list)
- frontend/components/FeasibilityScoreCard.tsx (create)
- frontend/lib/api/analysis.ts (modify — add getAggregatedResults())
- frontend/app/tenders/[id]/report/page.tsx (modify — add
  FeasibilityScoreCard below RiskRadarTable, replace the
  "Coming in full report" placeholder for Feasibility Score)

---

## What to implement

### 1. Add to frontend/lib/api/analysis.ts

  type DimensionScore = {
    score:     number   // 0-20
    rationale: string
  }

  type FeasibilityBreakdown = {
    technical_fit:      DimensionScore
    financial_capacity: DimensionScore
    timeline:           DimensionScore
    geographic_scope:   DimensionScore
    past_experience:    DimensionScore
  }

  type AggregatedResults = {
    feasibility_score:     number | null
    feasibility_breakdown: FeasibilityBreakdown | { error: string } | null
    risk_findings:         RiskFindingResponse[]
    financial_summary:     unknown   // REQ-006 pending
    source_languages:      string[]
  }

  getAggregatedResults(tenderId: string): Promise<AggregatedResults>
    - GET /tenders/{tenderId}/status
    - Extract aggregated_results from the response
    - On 404: return null
    - On 403: throw AuthError
    - On other errors: throw ApiError

### 2. frontend/components/FeasibilityScoreCard.tsx

Props:
  {
    tenderId:  string
    score:     number | null
    breakdown: FeasibilityBreakdown | { error: string } | null
  }

Layout — three sections stacked vertically:

  Section A — Composite Score Display:
    Large score number centred (e.g. "73")
    Label below: "out of 100"
    Colour-coded background ring or badge:
      0–39:  red    (#FEE2E2 bg, #B91C1C text)
      40–69: amber  (#FEF3C7 bg, #92400E text)
      70–100: green (#D1FAE5 bg, #065F46 text)
    Go/No-Go label below the score:
      0–39:  "High Risk — Consider Declining"
      40–69: "Moderate Fit — Review Carefully"
      70–100: "Strong Fit — Recommended to Bid"

    If score is null: show skeleton loader.
    If breakdown has "error" key: show amber banner:
      "Scoring encountered an issue. Manual review required."
      and hide the rest of the component.

  Section B — Dimension Breakdown Table:
    5 rows, one per dimension.
    Human-readable dimension labels (not raw keys):
      technical_fit      → "Technical Fit"
      financial_capacity → "Financial Capacity"
      timeline           → "Timeline"
      geographic_scope   → "Geographic Scope"
      past_experience    → "Past Experience"

    Each row shows:
      - Dimension label (left)
      - Score bar: filled portion = score/20 width
        colour matches the composite score colour rule
        (not the overall score — each dimension's own score)
      - Score fraction: "X / 20" (right)
      - Rationale text below the bar in muted small text
        Always fully visible — no truncation on rationale

  Section C — HITL Notice:
    Amber info banner:
      "This score is pending your review. You can adjust it
       before the final report is generated."
    Disabled "Approve & Adjust Score" button
    Small label below button: "HITL approval — coming in REQ-007"

### 3. Modify frontend/app/tenders/[id]/report/page.tsx

  - Import and render <FeasibilityScoreCard /> passing:
      tenderId={tenderId}
      score and breakdown fetched from getAggregatedResults()
  - Replace the existing "Coming in full report" placeholder
    for Feasibility Score with the real FeasibilityScoreCard
  - Keep <RiskRadarTable /> above FeasibilityScoreCard
  - Keep the "Financial Summary — Coming in full report"
    placeholder below FeasibilityScoreCard (REQ-006 pending)
  - Keep the disabled "Approve & Generate Full Report" button
    at the bottom (REQ-007 pending)

  Use TanStack Query v5 in the page to fetch aggregated results:
    useQuery({
      queryKey: ['aggregated-results', tenderId],
      queryFn: () => getAggregatedResults(tenderId),
      enabled: !!tenderId,
    })

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 3 files listed.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax — NOT v4.
- Use Shadcn/ui components for all UI elements.
- TypeScript strict mode — no `any` types.
- The score colour thresholds (0-39/40-69/70-100) must be
  applied independently per dimension bar AND for the composite
  score display — each dimension is coloured by its OWN score
  relative to 20, not by the composite score.
  Convert dimension score to percentage: (score / 20) * 100,
  then apply: 0-39% red, 40-69% amber, 70-100% green.
- If breakdown has an "error" key (malformed LLM response
  fallback from REQ-005 Slice 2), show the amber error banner
  and hide Section B — never crash trying to render breakdown
  fields that don't exist.
- The HITL notice (Section C) must always be visible when
  the run state is "awaiting_hitl" — never hidden even if
  score is null.
- All colours must use exact hex values provided above —
  consistent with REQ-004's RiskRadarTable colour system.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (3 files)
2. Confirm TanStack Query v5 useQuery syntax —
   show me the exact useQuery call in report/page.tsx
3. Confirm dimension colour is per-dimension, not composite —
   show me the colour calculation logic for a single dimension bar
4. Confirm the error state renders correctly — show me the
   condition check for breakdown?.error key
5. Open the report page in browser with a real completed run
   and show me:
   - Composite score displayed with correct colour
   - All 5 dimension bars with scores and rationales visible
   - Correct Go/No-Go label for the score range
   - HITL notice visible at the bottom

Do not move to Slice 5 until I explicitly tell you to.