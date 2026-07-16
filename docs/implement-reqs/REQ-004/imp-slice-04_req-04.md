Read the following documents before writing any code:
- docs/reqs/REQ-004_Risk_Radar_Node.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-004 — Slice 4 (Frontend) only**.

Slices 1, 2, and 3 are already complete. The following is available:
- GET /tenders/{id}/findings → returns list[RiskFindingResponse]
  ordered by severity (critical first) then confidence DESC
- RiskFindingResponse shape:
  {
    id: string (UUID)
    category: "fidic" | "penalty" | "lg_bond" | "termination" | "other"
    severity: "critical" | "high" | "medium" | "low"
    clause_text: string
    explanation: string
    source_chunk_index: number
    confidence: number (0.0 - 1.0)
  }
- The report page route is: /tenders/[id]/report
  (this page was stubbed in the AgentStreamViewer — now we build it)

---

## Your scope (do not touch anything outside this list)
- frontend/app/tenders/[id]/report/page.tsx (create)
- frontend/components/RiskRadarTable.tsx (create)
- frontend/lib/api/findings.ts (create — API client only)

---

## What to implement

### 1. frontend/lib/api/findings.ts

  type RiskFindingResponse = {
    id:                 string
    category:           'fidic' | 'penalty' | 'lg_bond' | 'termination' | 'other'
    severity:           'critical' | 'high' | 'medium' | 'low'
    clause_text:        string
    explanation:        string
    source_chunk_index: number
    confidence:         number
  }

  getFindings(tenderId: string): Promise<RiskFindingResponse[]>
    - GET /tenders/{tenderId}/findings
    - Authorization: Bearer from NEXT_PUBLIC_API_KEY
    - On 404: return [] (run not yet complete — not an error)
    - On 403: throw AuthError
    - On other errors: throw ApiError with status code

All API calls go through this file — no fetch() inside components.

### 2. frontend/components/RiskRadarTable.tsx

Props: { tenderId: string }

Data fetching:
  - Use TanStack Query v5:
    useQuery({ queryKey: ['findings', tenderId], queryFn: ... })
  - Show skeleton loader while loading
  - Show empty state if findings is [] with message:
    "No risk clauses identified in this tender."
  - Never show an error state for 404 — treat it as empty

Layout — findings grouped by severity, in this fixed order:
  CRITICAL → HIGH → MEDIUM → LOW
  Each group has a coloured section header:
    critical: red   background (#FEE2E2), text (#B91C1C)
    high:     amber background (#FEF3C7), text (#92400E)
    medium:   yellow background (#FEF9C3), text (#713F12)
    low:      grey  background (#F3F4F6), text (#374151)
  Only render a group if it has at least one finding.

Each finding row shows:
  - Severity badge (coloured chip, same colour as group header)
  - Category badge: human-readable label, not the raw enum value:
      fidic       → "FIDIC Clause"
      penalty     → "Penalty"
      lg_bond     → "LG / Bond"
      termination → "Termination"
      other       → "Other"
  - clause_text: truncated to 3 lines with a "Show full clause"
    expand/collapse toggle per row. The full text is always in the
    DOM (for copy/search) — only the visible height is toggled.
  - explanation: always fully visible, no truncation
  - Confidence indicator: a subtle horizontal bar (0-100%)
    below the explanation, labelled "Model confidence"
    colour: green if >= 0.8, amber if 0.5-0.79, grey if < 0.5
  - source_chunk_index shown as a small muted label:
    "Source: chunk #{source_chunk_index}"

Summary bar (above the grouped table):
  Show total finding count and a breakdown by severity:
  "12 findings — 2 Critical  •  4 High  •  5 Medium  •  1 Low"
  Each count is coloured to match its severity colour.
  If findings is []: hide the summary bar entirely.

### 3. frontend/app/tenders/[id]/report/page.tsx
Minimal page wrapper (full report UI comes in REQ-008):

  Layout:
  - Page title: "Risk Analysis Report"
  - Subtitle: "Tender ID: {tenderId}"
  - Info banner (amber): "This is a preliminary risk review.
    The full Go/No-Go report will be available after your
    review and approval below."
  - Renders <RiskRadarTable tenderId={tenderId} />
  - Below the table: a placeholder section titled
    "Feasibility Score" and "Financial Summary" with a
    grey "Coming in full report" label — these are REQ-005/006.
  - A disabled "Approve & Generate Full Report" button at the
    bottom, labelled: "HITL approval — coming in REQ-007"

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 3 files listed.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax throughout — NOT v4.
- Use Shadcn/ui components for all UI elements.
- TypeScript strict mode — no `any` types.
- The expand/collapse toggle for clause_text must be per-row,
  not a global "expand all" — each row manages its own state
  with useState.
- The severity group order (critical → high → medium → low)
  must be hardcoded — never infer order from the API response,
  since the backend already orders within groups but the frontend
  must group them.
- Category labels must use the human-readable mapping above —
  never display raw enum values (lg_bond, fidic, etc.) to the user.
- Confidence bar colour thresholds are: >= 0.8 green,
  0.5-0.79 amber, < 0.5 grey — use these exact breakpoints.
- All colours must use exact hex values provided above,
  not Tailwind colour names, to ensure severity colours are
  distinct and accessible.

---

## When you finish
Show me:
1. Full file tree of everything you created (3 files only)
2. Confirm TanStack Query v5 useQuery syntax —
   show me the exact useQuery call
3. Confirm category labels use human-readable mapping —
   show me the mapping object or switch statement in the code
4. Confirm clause_text expand/collapse is per-row with useState —
   show me the useState declaration and the toggle logic
5. Open the report page in browser with a real run that has
   findings and show me:
   - At least one finding in each severity group present in the data
   - clause_text truncates to 3 lines and expands on click
   - Confidence bar colour matches the threshold rules
   - Summary bar shows correct counts per severity

Do not move to Slice 5 until I explicitly tell you to.