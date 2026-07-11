Read the following documents before writing any code:
- docs/reqs/REQ-008_Report_Assembler.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-008 — Slice 4 (Frontend) only**.

Slice 3 is already complete. The following is available:
- GET /tenders/{id}/report → ReportResponse:
  {
    run_id, tender_id,
    go_no_go:               "GO" | "REVIEW" | "DECLINE",
    effective_score:        float,
    is_analyst_override:    bool,
    executive_summary:      str,
    recommendation:         str,
    risk_summary:           [{category, severity, description}]
                            (max 5 items),
    feasibility_highlights: [str] (3-5 items),
    financial_highlights:   [str] (3-5 items),
    analyst_note:           str | null,
    completed_at:           datetime | null
  }
- GET /tenders/{id}/status → includes report_available: bool
- app/tenders/[id]/report/page.tsx → existing report page
  with RiskRadarTable, FeasibilityScoreCard,
  FinancialSummaryCard, HITLGate — all from REQ-004 to 007
- HITLGate onApproved callback fires when run reaches
  "complete" state

---

## Your scope (do not touch anything outside this list)
- frontend/lib/api/report.ts (create)
- frontend/components/GoNoGoBadge.tsx (create)
- frontend/components/FullReportView.tsx (create)
- frontend/app/tenders/[id]/report/full/page.tsx (create)
- frontend/app/tenders/[id]/report/page.tsx (modify —
  wire HITLGate onApproved to navigate to full report)

---

## What to implement

### 1. frontend/lib/api/report.ts

  type RiskSummaryItem = {
    category:    string
    severity:    'critical' | 'high' | 'medium' | 'low'
    description: string
  }

  type ReportResponse = {
    run_id:                 string
    tender_id:              string
    go_no_go:               'GO' | 'REVIEW' | 'DECLINE'
    effective_score:        number
    is_analyst_override:    boolean
    executive_summary:      string
    recommendation:         string
    risk_summary:           RiskSummaryItem[]
    feasibility_highlights: string[]
    financial_highlights:   string[]
    analyst_note:           string | null
    completed_at:           string | null
  }

  getReport(tenderId: string): Promise<ReportResponse>
    - GET /tenders/{tenderId}/report
    - Authorization: Bearer from NEXT_PUBLIC_API_KEY
    - On 404: throw NotReadyError (not an error — report
      not yet available, caller handles gracefully)
    - On 403: throw AuthError
    - On other errors: throw ApiError with status code

  All API calls go through this file — no fetch() in
  components.

### 2. frontend/components/GoNoGoBadge.tsx

Props:
  {
    recommendation: 'GO' | 'REVIEW' | 'DECLINE'
    score:          number
    isOverride:     boolean
  }

Layout — a large prominent badge:

  GO:
    Background: #D1FAE5 (green-100)
    Border:     #065F46 (green-900)
    Text color: #065F46
    Icon:       ✅ (or Shadcn CheckCircle2)
    Label:      "GO — Recommended to Bid"
    Score display: "{score} / 100"

  REVIEW:
    Background: #FEF3C7 (amber-100)
    Border:     #92400E (amber-900)
    Text color: #92400E
    Icon:       ⚠️ (or Shadcn AlertTriangle)
    Label:      "REVIEW — Review Carefully Before Bidding"
    Score display: "{score} / 100"

  DECLINE:
    Background: #FEE2E2 (red-100)
    Border:     #B91C1C (red-900)
    Text color: #B91C1C
    Icon:       ❌ (or Shadcn XCircle)
    Label:      "DECLINE — Consider Not Bidding"
    Score display: "{score} / 100"

  If isOverride=True: show below the badge in muted text:
    "Score adjusted by analyst review
     (AI score: {aiScore} → Analyst score: {score})"
  Note: aiScore is not in ReportResponse — show only
  "Analyst-adjusted score" label without the original
  AI score (the full comparison is in HITLGate STATE 3).

### 3. frontend/components/FullReportView.tsx

Props: { tenderId: string }

Data fetching:
  useQuery({
    queryKey: ['report', tenderId],
    queryFn: () => getReport(tenderId),
    retry: (failureCount, error) =>
      error instanceof NotReadyError && failureCount < 10,
    retryDelay: 3000,
  })
  TanStack Query v5 syntax only.

  While loading or retrying (NotReadyError):
    Show skeleton with message:
    "Generating your Go/No-Go report...
     This usually takes 30–60 seconds."
    Animated Loader2 spinner (Shadcn/ui).

  On success: render the full report.
  On other errors (403, 500): show error banner.

Layout — 6 sections in fixed order:

  Section 1 — Go/No-Go Badge (full width, prominent)
    <GoNoGoBadge
      recommendation={report.go_no_go}
      score={report.effective_score}
      isOverride={report.is_analyst_override}
    />

  Section 2 — Executive Summary
    Card with title "Executive Summary"
    report.executive_summary as a paragraph
    Below: report.recommendation in bold italic
    Coloured left border matching Go/No-Go colour

  Section 3 — Top Risks (if risk_summary is non-empty)
    Title: "Key Risk Factors"
    Subtitle: "Top risks identified by AI analysis"
    Table with columns: Severity | Category | Summary
    Severity badge coloured per REQ-004 colour system:
      critical: red, high: amber, medium: yellow, low: grey
    Category: human-readable label (same mapping as
    RiskRadarTable from REQ-004)
    If risk_summary is empty: show info banner:
      "No significant risk clauses identified."

  Section 4 — Feasibility Highlights
    Title: "Feasibility Assessment"
    Subtitle: "Based on company profile matching"
    Bulleted list of report.feasibility_highlights
    Each bullet prefixed with ✓ in brand colour (#4F46E5)

  Section 5 — Financial Highlights
    Title: "Financial Commitments Summary"
    Bulleted list of report.financial_highlights
    Each bullet prefixed with $ in muted grey

  Section 6 — Analyst Note (only if analyst_note is set)
    Amber info card:
    Title: "Analyst Review Note"
    Content: report.analyst_note
    Small label: "Recorded in audit log — {completed_at}"

  PDF Download Button (below all sections):
    Label: "Download Report as PDF"
    Action: window.print() — uses browser print dialog
    Style: outlined button, brand colour
    Note: Add a print-specific CSS class that hides
    navigation and shows only the report content when
    printing. Use Tailwind's print: modifier.

### 4. frontend/app/tenders/[id]/report/full/page.tsx

  Minimal page wrapper:
  - Title: "Go/No-Go Report"
  - Subtitle: "TenderIQ Analysis — {tenderId}"
  - Renders <FullReportView tenderId={tenderId} />
  - Print-friendly: hide sidebar/navbar on print using
    Tailwind print:hidden on layout elements

### 5. Modify frontend/app/tenders/[id]/report/page.tsx
  One change only: wire HITLGate onApproved callback to
  navigate to the full report page:

  onApproved={() => {
    router.push(`/tenders/${tenderId}/report/full`)
  }}

  Replace the existing onApproved={() => refetch()} with
  the navigation above.

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 5 files.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax — NOT v4.
- Use Shadcn/ui components for all UI elements.
- TypeScript strict mode — no `any` types.
- The retry logic for NotReadyError must use TanStack
  Query's retry option — NOT a manual setInterval or
  useEffect polling loop.
- GoNoGoBadge colours must use exact hex values from
  REQ-004/005/006 colour system for consistency —
  not Tailwind colour names.
- The PDF download must use window.print() — not a
  server-side PDF generation endpoint (that is out of
  MVP scope).
- Section 3 (Top Risks) severity badge colours must
  be identical to RiskRadarTable from REQ-004 — same
  hex values, same category label mapping.
- Section 6 (Analyst Note) only renders when
  analyst_note is not null — never render an empty card.
- All 6 sections must appear in fixed order A→F regardless
  of report data content.

---

## When you finish
Show me:
1. Full file tree of everything created or modified (5 files)
2. Confirm TanStack Query v5 retry syntax for NotReadyError —
   show me the exact retry and retryDelay options
3. Confirm GoNoGoBadge uses exact hex values — show me
   the colour constants or the conditional colour logic
4. Confirm Section 6 (Analyst Note) only renders when
   analyst_note is not null — show me the conditional
5. Open the full report page with a real completed run
   and show me:
   - GoNoGoBadge renders in correct colour with score
   - Executive summary and recommendation visible
   - Risk summary table with severity badges
   - Feasibility and financial highlights as bullet lists
   - Analyst note card visible if is_analyst_override=True
   - PDF download button present

Do not move to Slice 5 until I explicitly tell you to.