Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md

You are implementing **REQ-009 — Slice 4 (Frontend) only**.

Slices 1, 2, and 3 are already complete. The following
is available:
- WS /tenders/{tender_id}/stream?token=<key> → streams
  StreamEvent JSON objects
- StreamEvent shape:
  {
    run_id:     string
    event_type: "node_started" | "node_completed" |
                "awaiting_hitl" | "resuming" |
                "complete" | "failed" |
                "cost_update" | "heartbeat"
    node_name:  string | null
    timestamp:  string (ISO 8601)
    data:       object
  }
- Existing polling components from REQ-003/007/008:
  AgentStreamViewer.tsx — polls GET /status every 2s
  HITLGate.tsx STATE 2 — polls GET /status every 3s
  FullReportView.tsx — retries GET /report every 3s

---

## Your scope (do not touch anything outside this list)
- frontend/hooks/useRunStream.ts (create)
- frontend/components/AgentStreamViewer.tsx (modify)
- frontend/components/HITLGate.tsx (modify)
- frontend/components/FullReportView.tsx (modify)

---

## What to implement

### 1. frontend/hooks/useRunStream.ts

  type ConnectionState =
    'connecting' | 'connected' | 'reconnecting' | 'closed' | 'error'

  type StreamEvent = {
    run_id:     string
    event_type: 'node_started' | 'node_completed' |
                'awaiting_hitl' | 'resuming' |
                'complete' | 'failed' |
                'cost_update' | 'heartbeat'
    node_name:  string | null
    timestamp:  string
    data:       Record<string, unknown>
  }

  type UseRunStreamReturn = {
    latestEvent:     StreamEvent | null
    connectionState: ConnectionState
    error:           string | null
  }

  export function useRunStream(
    tenderId: string | null
  ): UseRunStreamReturn

  Implementation requirements:

  Connection:
    ws_url = `${process.env.NEXT_PUBLIC_WS_BASE_URL}
              /tenders/${tenderId}/stream
              ?token=${process.env.NEXT_PUBLIC_API_KEY}`
    Use NEXT_PUBLIC_WS_BASE_URL (ws:// or wss://) —
    separate from NEXT_PUBLIC_API_BASE_URL (http/https).
    Add to .env.example:
      NEXT_PUBLIC_WS_BASE_URL=ws://localhost:8000

  Reconnect logic:
    On disconnect (NOT from terminal event):
      Attempt reconnect with exponential backoff:
        attempt 1: wait 1s
        attempt 2: wait 2s
        attempt 3: wait 4s
        attempt 4+: wait 30s (cap)
      Set connectionState="reconnecting" during wait
      Max reconnect attempts: 10
      After 10 attempts: connectionState="error"
    On close code 4003 (unauthorised):
      Set connectionState="error", do NOT reconnect
    On terminal event (complete or failed):
      Set connectionState="closed", do NOT reconnect

  State management:
    Use useRef for the WebSocket instance (not useState)
    to prevent re-renders on connection changes.
    Use useState only for: latestEvent, connectionState, error.

  Cleanup:
    On unmount: close WebSocket with code 1000 (normal).
    Cancel any pending reconnect timers.

  Null guard:
    If tenderId is null: return
      { latestEvent: null, connectionState: 'closed',
        error: null }
    Do not attempt connection.

### 2. Modify AgentStreamViewer.tsx
Replace the TanStack Query polling with useRunStream hook.
Keep polling as fallback when connectionState="error".

  Changes (minimal diff approach):

  ADD at top:
    import { useRunStream } from '@/hooks/useRunStream'

  ADD inside component:
    const { latestEvent, connectionState } =
      useRunStream(tenderId)

  MODIFY node state inference:
    Instead of inferring from agent_trace (polling data),
    also react to latestEvent:

    useEffect(() => {
      if (!latestEvent) return
      if (latestEvent.event_type === 'node_started' &&
          latestEvent.node_name) {
        setCurrentlyRunning(latestEvent.node_name)
      }
      if (latestEvent.event_type === 'node_completed' &&
          latestEvent.node_name) {
        setCompletedNodes(prev =>
          new Set([...prev, latestEvent.node_name!])
        )
      }
      if (latestEvent.event_type === 'awaiting_hitl') {
        setRunState('awaiting_hitl')
      }
    }, [latestEvent])

  KEEP the existing TanStack Query polling:
    Change refetchInterval to:
      connectionState === 'error' ? 2000 : false
    This means: poll only when WebSocket is unavailable.

  Connection status indicator (small, unobtrusive):
    Top-right corner of the component:
    - Green dot + "Live" when connectionState="connected"
    - Amber dot + "Reconnecting..." when "reconnecting"
    - Grey dot + "Polling" when "error" (fallback active)
    - No indicator when "closed" or "connecting"

### 3. Modify HITLGate.tsx
Replace STATE 2 polling with WebSocket event.

  Changes:
  ADD:
    import { useRunStream } from '@/hooks/useRunStream'

  ADD inside component:
    const { latestEvent, connectionState } =
      useRunStream(
        runState === 'resuming' ? tenderId : null
      )
    // Only connect when in resuming state —
    // null tenderId stops the hook from connecting

  MODIFY STATE 2 polling:
    Current: refetchInterval: runState === 'resuming'
             ? 3000 : false
    Replace with WebSocket event reaction:

    useEffect(() => {
      if (latestEvent?.event_type === 'complete') {
        onApproved()  // existing callback
      }
    }, [latestEvent])

    KEEP TanStack Query polling as fallback:
      refetchInterval: connectionState === 'error' &&
                       runState === 'resuming'
                       ? 3000 : false

### 4. Modify FullReportView.tsx
Replace retry logic with WebSocket event.

  Changes:
  ADD:
    import { useRunStream } from '@/hooks/useRunStream'

  ADD inside component:
    const { latestEvent, connectionState } =
      useRunStream(tenderId)

  ADD effect for complete event:
    useEffect(() => {
      if (latestEvent?.event_type === 'complete') {
        // Trigger immediate report fetch
        refetch()
      }
    }, [latestEvent])

  MODIFY TanStack Query retry:
    Current: retry: (failureCount, error) =>
               error instanceof NotReadyError &&
               failureCount < 10
    Replace with:
    retry: (failureCount, error) =>
      error instanceof NotReadyError &&
      connectionState === 'error' &&  // only retry if WS down
      failureCount < 10

    This means: if WebSocket is working, don't retry
    on 404 — wait for the complete event instead.
    If WebSocket fails, fall back to retry behaviour.

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any new pages or components beyond
  the 4 files listed.
- Do NOT add any new npm packages — use native
  WebSocket API (window.WebSocket), not a WS library.
- TypeScript strict mode — no `any` types.
- useRunStream must use useRef for the WebSocket
  instance — not useState — to avoid re-renders on
  connect/disconnect.
- The polling fallback must be preserved in ALL THREE
  components — never remove the refetchInterval logic,
  only gate it on connectionState === 'error'.
- Close code 4003 must stop reconnection — show me
  the close event handler that checks event.code.
- The connection status indicator in AgentStreamViewer
  must be small and unobtrusive — a coloured dot with
  text, not a modal or banner.
- Add NEXT_PUBLIC_WS_BASE_URL to .env.example —
  show me the addition in your summary.
- Never pass tender content or financial values through
  the WebSocket hook to other components — the hook
  only exposes event metadata.

---

## When you finish
Show me:
1. Full contents of useRunStream.ts
2. Confirm useRef is used for WebSocket — show me
   the useRef declaration
3. Confirm close code 4003 stops reconnection —
   show me the onclose handler
4. Confirm polling fallback is preserved in all 3
   components — show me the refetchInterval condition
   in each one
5. Confirm NEXT_PUBLIC_WS_BASE_URL added to .env.example
6. Open the analysis page with a real active run and
   show me:
   - "Live" green dot visible in AgentStreamViewer
   - Node transitions happen in real time (no 2s delay)
   - After HITL approval: HITLGate transitions to STATE 3
     immediately when complete event arrives
   - FullReportView loads report immediately on complete
     event without waiting for retry cycle

Do not move to Slice 5 until I explicitly tell you to.