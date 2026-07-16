Read the following documents before writing any code:
- docs/reqs/REQ-003_LangGraph_Analysis_Run.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-003 — Slice 4 (Frontend) only**.

Slices 1, 2, and 3 are already complete. The following are available:
- POST /tenders/{id}/analyse → HTTP 202 { run_id, status }
- GET /tenders/{id}/status  → { run_id, state, started_at,
                                completed_at, error_reason, agent_trace }
- GET /analytics/cost       → cost breakdown per run

state values returned by GET /status:
  pending | running | awaiting_hitl | complete | failed

agent_trace shape (JSONB from analysis_runs):
  {
    "supervisor":  { ...node output },
    "risk_radar":  { ...node output },
    "scorer":      { ...node output },
    "financial":   { ...node output },
    "aggregator":  { ...node output }
  }
  Keys appear in agent_trace only after the node has completed.
  Use this to infer which nodes have run vs are still pending.

---

## Your scope (do not touch anything outside this list)
- frontend/app/upload/page.tsx (modify — add auto-trigger logic only)
- frontend/app/tenders/[id]/page.tsx (create)
- frontend/components/AgentStreamViewer.tsx (create)
- frontend/lib/api/analysis.ts (create — API client functions only)

---

## What to implement

### 1. frontend/lib/api/analysis.ts
Two async functions:

triggerAnalysis(tenderId: string): Promise<{ run_id: string, status: string }>
  - POST /tenders/{tenderId}/analyse
  - Authorization: Bearer from NEXT_PUBLIC_API_KEY
  - On 409: throw a ConflictError with the server message
    (run already in progress — not a real error, handle gracefully in UI)
  - On 404: throw NotFoundError
  - On other errors: throw ApiError with status code

getRunStatus(tenderId: string): Promise<RunStatusResponse>
  - GET /tenders/{tenderId}/status
  - Authorization: Bearer from NEXT_PUBLIC_API_KEY
  - Returns RunStatusResponse typed as:

  type RunStatusResponse = {
    run_id:        string
    state:         'pending' | 'running' | 'awaiting_hitl'
                   | 'complete' | 'failed'
    started_at:    string
    completed_at:  string | null
    error_reason:  string | null
    agent_trace:   Record<string, unknown>
  }

All API calls go through this file — no fetch() calls inside components.

### 2. Modify frontend/app/upload/page.tsx
After a successful upload (POST /tenders/upload returns tender_id):
  a) Automatically call triggerAnalysis(tender_id)
  b) On success: redirect to /tenders/{tender_id}
  c) On 409 ConflictError (run already exists):
     also redirect to /tenders/{tender_id} — not an error state
  d) On other errors: show error banner, stay on upload page,
     do not redirect

Do NOT rewrite the entire upload page — add these 3 behaviours
to the existing post-upload handler only.

### 3. frontend/app/tenders/[id]/page.tsx
Page wrapper:
  - Extract tender_id from URL params
  - Title: "Tender Analysis"
  - Renders <AgentStreamViewer tenderId={tender_id} />
  - No other logic in this file

### 4. frontend/components/AgentStreamViewer.tsx
A polling component with these exact behaviours:

Props: { tenderId: string }

Polling logic:
  - On mount: call getRunStatus(tenderId) immediately
  - If state is "pending" or "running": poll every 2 seconds
  - If state is "awaiting_hitl", "complete", or "failed":
    stop polling (run is no longer progressing)
  - Use TanStack Query v5 with refetchInterval for polling:
    refetchInterval: (query) =>
      query.state.data?.state === 'pending' ||
      query.state.data?.state === 'running' ? 2000 : false

Node display:
  Show all 5 nodes in a fixed vertical list (always visible,
  never appear/disappear based on state):
    supervisor, risk_radar, scorer, financial, aggregator

  Each node shows one of 3 states:
    PENDING  — node key not yet in agent_trace
               grey dot + node name in muted colour
    RUNNING  — this node's key is absent from agent_trace BUT
               the previous node's key is present
               (infer "currently running" from trace gaps)
               blue pulsing dot + node name in normal colour
    COMPLETE — node key exists in agent_trace
               green checkmark + node name in normal colour

  Node order for RUNNING inference:
    supervisor → risk_radar, scorer, financial (all 3 run in
    parallel after supervisor) → aggregator

  Special case for parallel nodes (risk_radar, scorer, financial):
    All 3 show RUNNING simultaneously after supervisor completes
    and before all 3 appear in agent_trace.

Run state banner (above node list):
  pending:       "Preparing analysis..." (grey)
  running:       "Analysis in progress" (blue)
  awaiting_hitl: "Ready for your review" (amber) +
                 link to /tenders/{id}/report (create this
                 link even though the report page is REQ-008)
  complete:      "Analysis complete" (green)
  failed:        "Analysis failed" (red) + error_reason text

Timing display:
  Show elapsed time since started_at, updating every second
  while state is pending or running.
  Format: "2m 34s"
  Stop updating when state is awaiting_hitl, complete, or failed.

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any pages or components beyond the 4 files listed.
- Do NOT add any new npm packages — use only what is already
  in package.json.
- Use TanStack Query v5 syntax throughout:
  useQuery({ queryKey: [...], queryFn: ..., refetchInterval: ... })
  NOT the v4 pattern useQuery(key, fn, options).
- Use Shadcn/ui components for all UI elements.
- TypeScript strict mode — no `any` types.
- The RUNNING state inference must be client-side only —
  do not add any new backend endpoint for this.
- The elapsed timer must use setInterval inside a useEffect
  and clean up on unmount — no memory leaks.
- All API base URL from NEXT_PUBLIC_API_BASE_URL env variable.

---

## When you finish
Show me:
1. Full file tree of everything you created or modified (4 files)
2. Confirm TanStack Query v5 refetchInterval syntax is used —
   show me the exact useQuery call with refetchInterval
3. Confirm polling stops when state is awaiting_hitl/complete/failed —
   show me the refetchInterval condition logic
4. Confirm the elapsed timer cleans up on unmount —
   show me the useEffect cleanup function
5. Open the page in browser with an active run and show me:
   - Nodes transition from PENDING → RUNNING → COMPLETE
     as the graph progresses
   - Polling stops when state reaches awaiting_hitl
   - The "Ready for your review" banner appears with the report link

Do not move to Slice 5 until I explicitly tell you to.