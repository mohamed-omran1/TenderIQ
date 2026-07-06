Read the following documents before writing any code:
- docs/reqs/REQ-006_Financial_Analyst_Node.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-006 — Slice 4 (Frontend) only**.

Slices 1, 2, and 3 are already complete. The following is available:
- GET /tenders/{id}/financial → list[FinancialCommitmentResponse]
  ordered by commitment_type ASC

  FinancialCommitmentResponse shape:
  {
    id:                string (UUID)
    commitment_type:   "bond" | "liquidated_damages" |
                       "payment_milestone" | "retention" |
                       "advance_payment" | "contract_value"
    amount_value:      number | null
    amount_currency:   string | null   (ISO 4217 or "UNKNOWN")
    percentage:        number | null
    description:       string
    needs_review:      boolean
    source_chunk_index: number | null
  }

- app/tenders/[id]/report/page.tsx → exists from REQ-004/005,
  currently renders:
    <RiskRadarTable />
    <FeasibilityScoreCard />
    "Financial Summary — Coming in full report" placeholder
    Disabled "Approve & Generate Full Report" button

---

## Your scope (do not touch anything outside this list)
- frontend/components/FinancialSummaryCard.tsx (create)
- frontend/lib/api/financial.ts (create — API client only)
- frontend/app/tenders/[id]/report/page.tsx (modify —
  replace placeholder, add FinancialSummaryCard)

---

## What to implement

### 1. frontend/lib/api/financial.ts

  type FinancialCommitment = {
    id:                string
    commitment_type:   'bond' | 'liquidated_damages' |
                       'payment_milestone' | 'retention' |
                       'advance_payment' | 'contract_value'
    amount_value:      number | null
    amount_currency:   string | null
    percentage:        number | null
    description:       string
    needs_review:      boolean
    source_chunk_index: number | null
  }

  getFinancialCommitments(
    tenderId: string
  ): Promise<FinancialCommitment[]>
    - GET /tenders/{tenderId}/financial
    - Authorization: Bearer from NEXT_PUBLIC_API_KEY
    - On 404: return [] (not yet available — not an error)
    - On 403: throw AuthError
    - On other errors: throw ApiError with status code

  All API calls go through this file — no fetch() in components.

### 2. frontend/components/FinancialSummaryCard.tsx

Props: { tenderId: string }

Data fetching:
  useQuery({
    queryKey: ['financial', tenderId],
    queryFn: () => getFinancialCommitments(tenderId),
  })
  TanStack Query v5 syntax only.
  Show skeleton loader while loading.
  On empty array: show info banner:
    "No financial commitments identified in this tender."
    (not an error — valid outcome for simple tenders)

Layout — 6 dedicated sections, shown only if data exists
for that commitment_type. Never show an empty section.

  Section A — Contract Value (commitment_type="contract_value")
    Large display at the top of the card.
    Format: "{currency} {value:,.0f}"
    e.g. "SAR 35,000,000"
    If amount_currency = "UNKNOWN" or needs_review = true:
      show amber badge: "⚠ Currency requires review"
    If no contract_value entry: show "Contract value not stated"
    in muted text — never hide the section entirely.

  Section B — Performance Bonds (commitment_type="bond")
    Table with columns: Type | Amount | % of Contract | Conditions
    Human-readable bond_type labels (from description field,
    not the raw commitment_type):
      Show description field directly — it contains the
      plain-English conditions from the node.
    Format amount: "{currency} {value:,.0f}"
    If needs_review=true on any bond: amber row highlight
      + tooltip: "Currency or amount requires manual verification"
    If no bonds: do not render this section.

  Section C — Liquidated Damages
    (commitment_type="liquidated_damages")
    Dedicated display block (not a table — it's a single item):
      Rate: "{currency} {value:,.0f} {period}"
      e.g. "SAR 5,000 per day"
      Cap: "{currency} {cap_value:,.0f}" or
           "{cap_percentage}% of contract value"
           (use percentage field if amount_value is null)
    If no LD entry: do not render this section.

  Section D — Payment Schedule
    (commitment_type="payment_milestone")
    Timeline-style display (not a plain table):
      Each milestone as a card/row showing:
        - Description (left)
        - Trigger event in muted italic text below description
        - Amount or percentage (right):
            If amount_value is not null:
              "{currency} {value:,.0f}"
            Else if percentage is not null:
              "{percentage}% of contract value"
            Else: "Amount not specified"
    Order milestones by their DB insertion order (id ASC —
    already ordered by the endpoint).
    If no milestones: do not render this section.

  Section E — Retention
    (commitment_type="retention")
    Single line: "Retention: {percentage}% of contract value"
    If no retention entry: do not render this section.

  Section F — Advance Payment
    (commitment_type="advance_payment")
    Single line: "{currency} {value:,.0f}"
    If needs_review=true: amber badge "⚠ Requires review"
    If no advance_payment entry: do not render this section.

  Needs-review summary banner (below all sections):
    If ANY commitment has needs_review=true:
      Show amber banner at the bottom:
      "⚠ {count} item(s) require manual currency verification
       before using these figures in a bid decision."
    If zero needs_review items: hide this banner.

### 3. Modify frontend/app/tenders/[id]/report/page.tsx
  - Import and render <FinancialSummaryCard tenderId={tenderId} />
  - Replace the "Financial Summary — Coming in full report"
    placeholder with the real FinancialSummaryCard
  - Position: below FeasibilityScoreCard, above the
    disabled "Approve & Generate Full Report" button
  - No other changes to this file

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 3 files listed.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax — NOT v4.
- Use Shadcn/ui components for all UI elements.
- TypeScript strict mode — no `any` types.
- Format all monetary values with thousand separators
  (toLocaleString() or equivalent) — never show raw floats
  like "35000000" to the user.
- Never show amount_currency = "UNKNOWN" as-is to the user —
  replace with the amber "⚠ Requires review" badge.
- Never render a section that has no data — only render
  sections where at least one commitment of that type exists.
  Exception: Section A (contract_value) always renders,
  even if showing "Contract value not stated".
- The 6 sections must always appear in the order A→F
  regardless of the API response order.
- On 404 from the API (financial not yet available):
  show the info banner "No financial commitments identified"
  — do not show an error state.
- TypeScript: commitment_type must be typed as a union
  literal type, not string — use the FinancialCommitment
  type defined above throughout.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (3 files)
2. Confirm TanStack Query v5 useQuery syntax —
   show me the exact useQuery call
3. Confirm UNKNOWN currency is never shown raw —
   show me the condition check that replaces it with the
   amber badge
4. Confirm sections only render when data exists —
   show me the conditional render logic for Section B (bonds)
   and Section D (payment schedule)
5. Open the report page in browser with a real completed
   run that has financial data and show me:
   - Contract value displayed with correct currency and
     thousand separators
   - At least one bond row in the bonds table
   - Liquidated damages rate and cap visible
   - needs_review amber badge visible if any item has
     needs_review=true
   - Sections with no data are hidden

Do not move to Slice 5 until I explicitly tell you to.