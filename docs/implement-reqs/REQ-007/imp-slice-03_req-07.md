Read the following documents before writing any code:
- docs/reqs/REQ-007_HITL_Override_Gate.md

You are implementing **REQ-007 — Slice 3 (Frontend Polish) only**.

Slice 2 is already complete. The following is available:
- frontend/components/HITLGate.tsx → working component with
  3 states (awaiting_hitl, resuming, complete)
- frontend/lib/api/hitl.ts → approveRun() + overrideRun()
- POST /tenders/{id}/approve → HTTP 202
- POST /tenders/{id}/override → HTTP 202
- GET /tenders/{id}/financial → list[FinancialCommitment]
  with needs_review: boolean per item (REQ-006)
- hitl_overrides table → has action, original_score,
  overridden_score per run

---

## Your scope (do not touch anything outside this list)
- frontend/components/HITLGate.tsx (extend — 2 additions only)
- frontend/lib/api/hitl.ts (extend — add getHITLOverride())

---

## What to implement

### 1. Add to frontend/lib/api/hitl.ts

  type HITLOverride = {
    run_id:           string
    action:           'approved' | 'overridden'
    original_score:   number
    overridden_score: number | null
    justification:    string | null
    created_at:       string
  }

  getHITLOverride(tenderId: string): Promise<HITLOverride | null>
    - GET /tenders/{tenderId}/hitl-override
    - Authorization: Bearer from NEXT_PUBLIC_API_KEY
    - On 404: return null (no override yet — not an error)
    - On 403: throw AuthError
    - On other errors: throw ApiError

  Note: you also need to add this endpoint to the backend.
  Add GET /tenders/{tender_id}/hitl-override to
  app/api/routers/tenders.py:
    - Resolve company_id from API key
    - Fetch analysis_run for this tender
    - Authorisation check
    - Query hitl_overrides WHERE run_id = run.id
    - If not found: HTTP 404
    - Return HITLOverrideResponse (add to schemas/analysis.py):
        run_id, action, original_score, overridden_score,
        justification (EXCLUDE justification from response
        if it contains sensitive content — return null for
        justification in the API response even if stored in DB.
        The justification is an internal audit field, not
        for display.)

    Wait — re-read the security rule below before deciding
    whether to include justification in the response.

### 2. Addition A — needs_review warning banner
Add to HITLGate.tsx, rendered at the TOP of STATE 1
(above the score display), ONLY when needs_review items exist:

  Data fetching:
    useQuery({
      queryKey: ['financial', tenderId],
      queryFn: () => getFinancialCommitments(tenderId),
    })
    Import getFinancialCommitments from lib/api/financial.ts
    (already exists from REQ-006 — do not re-implement)

  Banner condition:
    const needsReviewCount = financialData?.filter(
      c => c.needs_review
    ).length ?? 0

    Show banner only if needsReviewCount > 0

  Banner UI (amber, above score section):
    "⚠ {needsReviewCount} financial item(s) have unverified
    currencies and require manual review before approval.
    See the Financial Summary above for details."

    The banner is informational — it does NOT block the
    approve/override action. The analyst can still approve
    even if needs_review items exist.

### 3. Addition B — override history display
Add to HITLGate.tsx, rendered in STATE 3 (complete) only,
below the "✅ Report Generated" message:

  Data fetching:
    useQuery({
      queryKey: ['hitl-override', tenderId],
      queryFn: () => getHITLOverride(tenderId),
    })

  Display conditions:
    If override is null or action = "approved":
      Show: "Score approved as-is: {original_score} / 100"
      Grey muted text, no emphasis

    If action = "overridden":
      Show a comparison block:
        "Score adjusted by analyst review:"
        AI Score:       {original_score} / 100  [strikethrough]
        Final Score:    {overridden_score} / 100 [bold, green]
        Small label: "Analyst override recorded in audit log"

    Never display justification text in the UI —
    it is an internal audit field only.

---

## Rules
- Do NOT rewrite HITLGate.tsx from scratch — add the two
  additions (banner + override history) to the existing
  component only. Minimise diff.
- Do NOT modify any other frontend files beyond the 2 listed.
- The needs_review banner must reuse getFinancialCommitments()
  from lib/api/financial.ts — never re-fetch financial data
  with a new fetch() call or duplicate function.
- The override history display must show original_score with
  strikethrough styling when action = "overridden" — use
  Tailwind's line-through class.
- justification must NEVER be displayed in the UI anywhere —
  not in the override history, not in a tooltip, not in
  any log or console statement.
- TypeScript strict mode — no `any` types.
- Do NOT add any new npm packages.
- The backend endpoint GET /tenders/{id}/hitl-override must
  NOT return justification in its response body — omit the
  field entirely or return null. Justification is stored
  in the DB for audit purposes only, never surfaced to the UI.

---

## When you finish
Show me:
1. Full file tree of everything created or modified (3 files:
   HITLGate.tsx, hitl.ts, routers/tenders.py)
2. Confirm needs_review banner uses getFinancialCommitments()
   from lib/api/financial.ts — show me the import line
3. Confirm justification is NOT in the API response —
   show me the HITLOverrideResponse schema definition
4. Confirm justification is NOT displayed anywhere in the UI —
   grep HITLGate.tsx for "justification" and show output
   (should only appear in type definitions, never in JSX)
5. Open the report page with a completed override run and
   show me:
   - STATE 3 shows original_score with strikethrough
   - STATE 3 shows overridden_score in bold green
   - "Analyst override recorded in audit log" label visible
   - needs_review banner visible in STATE 1 if financial
     items have needs_review=True

Do not move to Slice 4 until I explicitly tell you to.