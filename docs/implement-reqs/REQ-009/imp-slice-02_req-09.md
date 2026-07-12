Read the following documents before writing any code:
- docs/reqs/REQ-009_WebSocket_Streaming.md

You are implementing **REQ-009 — Slice 2 (Backend Publish) only**.

Slice 1 is already complete. The following is available:
- app/services/event_bus.py → EventBus class,
  get_event_bus(), publish_event(), publish_heartbeat()
- app/schemas/stream.py → StreamEvent, make_event()
- event_bus singleton initialised in app/main.py lifespan

---

## Your scope (do not touch anything outside this list)
- app/api/routers/tenders.py (modify run_graph() and
  _resume_graph() — add publish calls only)
- app/middleware/cost_tracker.py (modify on_llm_end()
  — add cost_update publish only)

---

## What to implement

### 1. Modify run_graph() in routers/tenders.py
Find the existing async for event in graph.astream() loop.
Add publish calls around each step:

  async for event in graph.astream(initial_state, config):
      node_name = list(event.keys())[0]

      # Publish node_started BEFORE DB write
      await event_bus.publish_event(run_id_str, make_event(
          run_id_str, "node_started",
          node_name=node_name,
      ))

      # Existing DB agent_trace update (unchanged)
      await db.execute(update(AnalysisRun)
          .where(AnalysisRun.id == run_id)
          .values(agent_trace=AnalysisRun.agent_trace.concat(
              {node_name: event[node_name]}
          )))

      # Publish node_completed AFTER DB write
      await event_bus.publish_event(run_id_str, make_event(
          run_id_str, "node_completed",
          node_name=node_name,
      ))

  # After loop ends — graph paused at HITL gate
  # Existing DB state update (unchanged)
  await db.execute(update(AnalysisRun) ...)
  await db.commit()

  # Publish awaiting_hitl AFTER commit
  # Include summary data for the HITLGate component
  final_checkpoint = await graph.aget_state(config)
  await event_bus.publish_event(run_id_str, make_event(
      run_id_str, "awaiting_hitl",
      data={
          "feasibility_score": final_checkpoint.values.get(
              "feasibility_score"
          ),
          "risk_count": len(
              final_checkpoint.values.get("risk_findings", [])
          ),
      }
  ))

### 2. Modify _resume_graph() in routers/tenders.py
Add publish calls at resume start and completion:

  async def _resume_graph(run_id, override_score):
    try:
      # Publish resuming immediately
      await event_bus.publish_event(str(run_id), make_event(
          str(run_id), "resuming",
          data={
              "action": "overridden" if override_score
                        is not None else "approved",
              "effective_score": override_score,
          }
      ))

      # ... existing graph.aupdate_state() and
      # graph.astream(None, config) logic (unchanged) ...

      # Publish complete after DB commit
      final_state = await graph.aget_state(config)
      report = final_state.values.get("final_report", {})
      await event_bus.publish_event(str(run_id), make_event(
          str(run_id), "complete",
          data={
              "go_no_go": report.get("go_no_go", "REVIEW"),
              "effective_score": report.get(
                  "effective_score", 0.0
              ),
          }
      ))

    except Exception as e:
      # Publish failed event before setting DB state
      await event_bus.publish_event(str(run_id), make_event(
          str(run_id), "failed",
          data={"error_reason": str(e)}
      ))
      # Existing DB failure handling (unchanged)
      await db.execute(...)

### 3. Modify CostTrackingHandler in cost_tracker.py
Add cost_update publish at end of on_llm_end():

  async def on_llm_end(self, response, **kwargs):
      # ... existing DB insert logic (unchanged) ...
      await self.db.commit()

      # Publish cost_update (after DB commit)
      # Import lazily to avoid circular imports
      try:
          from app.services.event_bus import get_event_bus
          from app.schemas.stream import make_event
          bus = get_event_bus()
          await bus.publish_event(
              self.run_id,
              make_event(
                  self.run_id, "cost_update",
                  node_name=self.node_name,
                  data={"cost_usd": round(cost, 6)},
              )
          )
      except Exception:
          pass  # cost_update publish failure never raises

---

## Rules
- Do NOT modify agents/graph.py, agents/state.py,
  or any node files.
- Do NOT create the WebSocket endpoint — that is Slice 3.
- Do NOT create any frontend files.
- All publish_event() calls must be wrapped in the
  event_bus's own silent-failure handling (already
  implemented in Slice 1) — no additional try/except
  needed in the router unless you want extra safety.
- Publish node_started BEFORE the DB write, publish
  node_completed AFTER the DB write — this ensures
  the client sees "node started" before data is persisted.
- Publish awaiting_hitl AFTER db.commit() — client
  must not receive this event before the DB state is
  consistent.
- Publish complete AFTER db.commit() in _resume_graph()
  — same reason.
- cost_update publish must use lazy import to avoid
  circular imports between cost_tracker and event_bus.
- Do NOT include financial values, clause text, or
  any tender content in event data dicts.
- The run_id passed to publish_event must be a string —
  UUID objects must be converted with str().

---

## When you finish
Show me:
1. Full diff of changes to routers/tenders.py —
   show only the added lines (not the full file)
2. Full diff of changes to cost_tracker.py —
   show only the added lines
3. Run a full analysis end-to-end and capture
   Redis events using redis-cli:
   # In one terminal:
   redis-cli subscribe "run:<your_run_id>"
   # In another terminal: trigger analysis
   # Show me the actual Redis output — the sequence
   # of events as they arrive
   Expected sequence:
     node_started (supervisor)
     node_completed (supervisor)
     node_started (risk_radar)
     node_started (scorer)
     node_started (financial)
     node_completed (risk_radar)
     node_completed (scorer)
     node_completed (financial)
     node_started (aggregator)
     node_completed (aggregator)
     awaiting_hitl
     [after HITL approval:]
     resuming
     node_started (report_assembler)
     node_completed (report_assembler)
     complete
4. Confirm awaiting_hitl is published AFTER db.commit()
   — show me the ordering of these two lines in the code
5. Confirm cost_update publish uses lazy import —
   show me the import statement inside on_llm_end()

Do not move to Slice 3 until I explicitly tell you to.