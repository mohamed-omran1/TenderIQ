Read the following documents before writing any code:
- docs/reqs/REQ-007_HITL_Override_Gate.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-007 — Slice 2 (Frontend) only**.

Slice 1 is already complete. The following is available:
- POST /tenders/{id}/approve → HTTP 202 HITLResponse
  body: { run_id, action, original_score,
          overridden_score, message }
- POST /tenders/{id}/override → HTTP 202 HITLResponse
  same body shape
- GET /tenders/{id}/status → includes state field:
  "awaiting_hitl" | "resuming" | "complete" | "failed"
- app/tenders/[id]/report/page.tsx → exists, currently renders:
    <RiskRadarTable />
    <FeasibilityScoreCard /> (with disabled "Approve" button)
    <FinancialSummaryCard />
    Disabled "Approve & Generate Full Report" button at bottom

---

## Your scope (do not touch anything outside this list)
- frontend/components/HITLGate.tsx (create)
- frontend/lib/api/hitl.ts (create — API client only)
- frontend/app/tenders/[id]/report/page.tsx (modify —
  replace the disabled button with real HITLGate component)

---

## What to implement

### 1. frontend/lib/api/hitl.ts

  type HITLResponse = {
    run_id:           string
    action:           'approved' | 'overridden'
    original_score:   number
    overridden_score: number | null
    message:          string
  }

  approveRun(tenderId: string, justification?: string):
    Promise<HITLResponse>
    - POST /tenders/{tenderId}/approve
    - Body: { justification } (optional)
    - Authorization: Bearer from NEXT_PUBLIC_API_KEY
    - On 409: throw ConflictError (already approved or
      wrong state — show specific message to user)
    - On 403: throw AuthError
    - On 422: throw ValidationError with field detail
    - On other errors: throw ApiError

  overrideRun(
    tenderId: string,
    overriddenScore: number,
    justification: string
  ): Promise<HITLResponse>
    - POST /tenders/{tenderId}/override
    - Body: { overridden_score: overriddenScore, justification }
    - Same error handling as approveRun

  All API calls go through this file — no fetch() in components.

### 2. frontend/components/HITLGate.tsx

Props:
  {
    tenderId:       string
    currentScore:   number        // AI feasibility score
    runState:       string        // current analysis_runs.state
    onApproved:     () => void    // callback after success
  }

The component has 3 distinct visual states:

  STATE 1 — "awaiting_hitl" (analyst must act)
  ─────────────────────────────────────────────
  Amber bordered card with header:
    "⚠ Analyst Review Required"
    Subtitle: "Review the findings above before generating
    the final report. You may approve the AI score or
    adjust it based on your expert judgment."

  Score display section:
    Label: "AI Feasibility Score"
    Large number: {currentScore} / 100
    Colour: red (<40) / amber (40-69) / green (>=70)
    Same colour thresholds as FeasibilityScoreCard

  Score adjustment section:
    Toggle: "Adjust score" (default: off)
    When toggle is ON:
      Slider: min=0, max=100, step=1
      Default slider value = currentScore
      Live display: "New score: {sliderValue} / 100"
      Colour updates live as slider moves
      Justification textarea:
        placeholder: "Explain why you are adjusting the
        score (required when changing the score)..."
        min 10 characters — show character count
        Show inline error "Justification required
        (minimum 10 characters)" if submit attempted
        with fewer than 10 chars

    When toggle is OFF:
      No slider, no justification field
      Optional justification textarea:
        placeholder: "Add a note (optional)..."
        No minimum length

  Action button:
    When toggle OFF: "Approve AI Score"
      → calls approveRun(tenderId, justification?)
    When toggle ON and slider != currentScore:
      "Override Score to {sliderValue}"
      → calls overrideRun(tenderId, sliderValue, justification)
    When toggle ON and slider == currentScore:
      "Approve with Note" (treated as approve, not override)
      → calls approveRun(tenderId, justification)

    Button loading state: "Processing..." while API call
    is in flight. Disable button during loading.

  STATE 2 — "resuming" (report generation in progress)
  ──────────────────────────────────────────────────────
  Blue info card:
    "📄 Generating Report..."
    "The final report is being assembled. This usually
    takes 30–60 seconds."
    Animated spinner (Shadcn/ui Loader2 icon)
    Poll GET /tenders/{id}/status every 3 seconds
    When state transitions to "complete": call onApproved()

  STATE 3 — "complete" (report generated)
  ─────────────────────────────────────────
  Green success card:
    "✅ Report Generated"
    "The Go/No-Go report has been generated."
    If action was "overridden":
      Show: "Score adjusted: {original_score} → {overridden_score}"
    Button: "View Full Report" → navigate to
    /tenders/{tenderId}/report/full
    (this page is REQ-008 — button navigates but page
    shows "coming soon" for now)

### 3. Modify frontend/app/tenders/[id]/report/page.tsx
  - Import and render <HITLGate /> at the bottom of the page
    replacing the existing disabled "Approve & Generate
    Full Report" button
  - Pass:
      tenderId={tenderId}
      currentScore={feasibilityScore ?? 0}
      runState={runStatus?.state ?? 'awaiting_hitl'}
      onApproved={() => refetch()}
  - When onApproved fires (report complete): refetch the
    run status so the page reflects the new state

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 3 files listed.
- Do NOT add any new npm packages.
- Use TanStack Query v5 syntax throughout — NOT v4.
- Use Shadcn/ui components for all UI elements:
  Slider, Textarea, Button, Badge, Card — do not build
  custom HTML inputs.
- TypeScript strict mode — no `any` types.
- The score colour thresholds must be consistent with
  FeasibilityScoreCard: <40 red, 40-69 amber, >=70 green,
  exact same hex values.
- The slider default value must always equal currentScore
  when the adjust toggle is first turned on — never 0 or 50.
- When toggle is turned OFF after being ON, reset slider
  to currentScore and clear justification — do not
  preserve a partially-filled state.
- The "Processing..." loading state must disable the button
  AND prevent the toggle from being changed — lock the
  entire form during submission.
- Poll interval for STATE 2 must be 3 seconds using
  TanStack Query v5 refetchInterval — not setInterval.
- justification text must never appear in any console.log
  or error message sent to the server beyond the API call
  body itself.

---

## When you finish
Show me:
1. Full file tree of everything created or modified (3 files)
2. Confirm Shadcn/ui Slider is used — show me the import
   and the Slider JSX
3. Confirm slider default = currentScore when toggle turns on —
   show me the useState initialisation and the toggle handler
4. Confirm TanStack Query v5 refetchInterval for STATE 2 polling —
   show me the exact useQuery call with refetchInterval
5. Open the report page with a real run in "awaiting_hitl"
   state and show me:
   - STATE 1 renders with correct AI score and colour
   - Toggle turns on and slider appears at currentScore
   - Approve button label changes based on toggle state
   - After approve: transitions to STATE 2 spinner
   - After resume completes: transitions to STATE 3 success

Do not move to Slice 3 until I explicitly tell you to.