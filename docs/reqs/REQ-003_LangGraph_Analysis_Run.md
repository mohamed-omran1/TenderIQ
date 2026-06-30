```python
md_content = """# REQ-003: LangGraph Analysis Run — Supervisor & Orchestration

| Property | Value |
| --- | --- |
| **Status** | READY FOR IMPLEMENTATION |
| **Sprint** | Week 2 — Core Agents |
| **Priority** | P0 — Blocking (REQ-004, REQ-005, REQ-006 all depend on this graph skeleton) |
| **Dependencies** | REQ-001 complete (tender_chunks available), REQ-002 complete (profile_lookup tool available) |
| **Related Docs** | TenderIQ_Architecture_v1.0 §3 (LangGraph Implementation Detail), §2 (Request Lifecycle) |

## Owning Component

| FastAPI Router | LangGraph StateGraph | analysis_runs table | Cost Tracker Middleware |
| --- | --- | --- | --- |
| routers/tenders.py | agents/graph.py + agents/state.py | db/models.py | middleware/cost_tracker.py |

## Description
Implement the core LangGraph StateGraph that orchestrates the full TenderIQ analysis pipeline.

This REQ delivers the graph skeleton, the shared TenderState, the Supervisor node, the fan-out/fan-in wiring to the three specialist agent nodes (Risk Radar, Feasibility Scorer, Financial Analyst), the Results Aggregator node, and the cost-tracking callback middleware.

The specialist agent nodes themselves (REQ-004, REQ-005, REQ-006) are stubbed here with deterministic outputs so the graph is fully runnable and testable end-to-end before real LLM logic is added.

This REQ also delivers the POST /tenders/{id}/analyse endpoint that launches a graph run asynchronously and the GET /tenders/{id}/status endpoint for polling run state.

## Preconditions
* REQ-001 Slice 1 complete: tenders table exists, POST /tenders/upload works, tender_chunks are populated for a given tender_id.
* REQ-002 Slice 2 complete: profile_lookup LangChain tool is available and independently tested.
* A valid company profile exists for the test company (needed for Supervisor to fetch profile at run start).
* OPENAI_API_KEY (or equivalent LLM provider key) is set in .env — required even for stubbed nodes that make no real LLM calls, because the LangChain callback handler must initialise.
* PostgreSQL checkpointer table (langgraph_checkpoints) exists — created via LangGraph's built-in setup method on app startup.

## Main Flow

### POST /tenders/{id}/analyse
1. Client sends POST /tenders/{id}/analyse with a valid API key.
2. FastAPI validates that the tender exists, belongs to the authenticated company, and has status = "ready".
3. Any other status returns an error (see Alternative Flows).
4. Backend creates an analysis_runs row with state = "pending" and returns HTTP 202 with { run_id, status: "pending" } immediately.
5. A background task invokes graph.astream() with a fresh TenderState populated with tender_id, run_id, company_id, and the pre-fetched tender chunks.
6. The Supervisor node runs first: fetches the company profile via profile_lookup tool, validates chunks are non-empty, and sets state.supervisor_ready = True.
7. LangGraph fans out to Risk Radar, Feasibility Scorer, and Financial Analyst nodes concurrently (parallel branches).
8. Each stubbed node writes a placeholder result to TenderState and returns.
9. The Results Aggregator node runs after all three parallel branches complete, merging their outputs into state.aggregated_results.
10. The graph reaches the interrupt_before=["report_assembler"] gate and pauses. analysis_runs.state becomes "awaiting_hitl".
11. Throughout the run, the CostTrackingHandler callback writes one llm_cost_events row per LLM call per node (zero rows for stubbed nodes in this REQ — real rows from REQ-004 onwards).

### GET /tenders/{id}/status
1. Client polls GET /tenders/{id}/status with a valid API key.
2. Backend queries analysis_runs for the latest run for this tender_id.
3. Returns HTTP 200 with { run_id, state, started_at, completed_at } — state reflects the current LangGraph checkpoint state.

## Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| Tender status is not "ready" (still uploading or failed) | HTTP 409 — "Tender is not ready for analysis. Current status: <status>." | No analysis_runs row created. |
| Tender belongs to a different company | HTTP 403 — "Not authorised to analyse this tender." | No analysis_runs row created. |
| A run is already in progress for this tender | HTTP 409 — "An analysis run is already in progress for this tender." | Existing run continues unaffected. |
| Company profile does not exist (Supervisor check fails) | Graph terminates early. analysis_runs.state = "failed", error = "No company profile found." | Run marked failed. Client sees "failed" on next status poll. |
| tender_chunks is empty for this tender | Graph terminates early. analysis_runs.state = "failed", error = "No content chunks found for tender." | Run marked failed. |
| LLM API call fails mid-run (REQ-004+ only) | Retry with exponential backoff (3 attempts). On exhausted retries: graph terminates, state = "failed". | Checkpoint preserved — run not resumable after failure. |

## Postconditions
* On successful run to HITL gate: analysis_runs.state = "awaiting_hitl", agent_trace JSONB contains the output of every node that ran, aggregated_results contains merged placeholder data from all three stub nodes.
* On failure: analysis_runs.state = "failed" with a human-readable error reason.
* No partial state is left in the checkpoint that could cause a resume attempt.
* The run_id is stable and referenced by all downstream REQs (HITL override, report assembly, cost analytics).
* LangGraph checkpoint is persisted to Postgres — the run survives a server restart while awaiting HITL approval.

## TenderState Schema
All nodes communicate exclusively through this TypedDict. No node may pass data to another node through any mechanism other than TenderState.

Define this in `agents/state.py`.

```python
from typing import TypedDict, Optional

class TenderState(TypedDict):
    # Identity
    tender_id:       str
    run_id:          str
    company_id:      str
    # Ingestor output (populated before graph starts)
    chunks:          list[dict]   # {content, detected_language, chunk_index}
    # Supervisor
    supervisor_ready: bool
    # Specialist node outputs (stubbed in REQ-003)
    risk_findings:   list[dict]
    feasibility_score: Optional[float]
    feasibility_breakdown: Optional[dict]
    financial_summary: Optional[dict]
    # Aggregator
    aggregated_results: Optional[dict]
    # HITL (REQ-007)
    hitl_approved:   bool
    hitl_override_score: Optional[float]
    # Report (REQ-008)
    final_report:    Optional[str]
    # Cost tracking
    token_usage:     list[dict]   # accumulates per node
    source_languages: list[str]   # detected in tender

```

## Graph Wiring

Implement in `agents/graph.py`. The graph must be compiled once at application startup and reused across all runs — do not recompile per request.

```python
builder = StateGraph(TenderState)

# Nodes
builder.add_node("supervisor",   supervisor_node)
builder.add_node("risk_radar",   risk_radar_node)    # stubbed in REQ-003
builder.add_node("scorer",       feasibility_scorer_node)  # stubbed
builder.add_node("financial",    financial_analyst_node)   # stubbed
builder.add_node("aggregator",   results_aggregator_node)
builder.add_node("report_assembler", report_assembler_node)  # stubbed

# Edges — fan-out from supervisor to 3 parallel branches
builder.set_entry_point("supervisor")
builder.add_edge("supervisor",  "risk_radar")
builder.add_edge("supervisor",  "scorer")
builder.add_edge("supervisor",  "financial")

# Fan-in — all 3 must complete before aggregator
builder.add_edge(["risk_radar", "scorer", "financial"], "aggregator")
builder.add_edge("aggregator",  "report_assembler")
builder.add_edge("report_assembler", END)

graph = builder.compile(
    checkpointer=PostgresSaver.from_conn_string(DATABASE_URL),
    interrupt_before=["report_assembler"],
)

```

## Stub Node Contracts

Each stub node must write to TenderState exactly as the real node will in REQ-004/005/006.

The data is placeholder but the structure must be final — changing the schema later will break the aggregator.

| Node | Writes to TenderState | Stub Value |
| --- | --- | --- |
| risk_radar | `state["risk_findings"]` | `[{"category": "stub", "severity": "low", "clause_text": "STUB", "explanation": "Stub — REQ-004 pending"}]` |
| scorer | `state["feasibility_score"]`, `state["feasibility_breakdown"]` | `score=0.0, breakdown={"stub": True}` |
| financial | `state["financial_summary"]` | `{"stub": True, "bonds": [], "commitments": []}` |
| aggregator | `state["aggregated_results"]` | Merges risk_findings + feasibility_score + financial_summary into one dict. This is real logic, not a stub. |

## Cost Tracking Middleware

Implement in `middleware/cost_tracker.py`. The CostTrackingHandler must be attached to every LLM client instantiated in every node.

In REQ-003 (stub nodes), no real LLM calls are made, so no rows are written — but the handler must be wired and tested so REQ-004 onwards work without changes to this file.

```python
class CostTrackingHandler(BaseCallbackHandler):
    def __init__(self, run_id: str, node_name: str, db: AsyncSession):
        self.run_id   = run_id
        self.node_name = node_name
        self.db       = db

    async def on_llm_end(self, response, **kwargs):
        usage = response.llm_output.get("usage", {})
        cost  = compute_cost(response.llm_output["model_name"], usage)
        await self.db.execute(insert(LlmCostEvent).values(
            run_id=self.run_id, 
            node_name=self.node_name,            
            model=response.llm_output["model_name"],            
            input_tokens=usage["prompt_tokens"],            
            output_tokens=usage["completion_tokens"],            
            cost_usd=cost,        
        ))

```

## Non-Functional Requirements

### Performance

* POST /tenders/{id}/analyse must respond in under 500ms — graph execution is async background, not blocking.
* The compiled graph object is created once at startup — startup time may increase by up to 3 seconds for checkpointer initialisation, which is acceptable.

### Reliability

* LangGraph checkpoint must survive a server restart — verified by killing the process while a run is in "awaiting_hitl" state and confirming the run_id is still resumable after restart.
* A failed run must never leave analysis_runs in a permanent "running" state — every failure path sets state = "failed".

### Security

* A company may only trigger analysis on their own tenders — company_id from API key must match tender.company_id before the run is created.
* run_id is a UUID generated server-side — never accepted from the client.

## Implementation Slices

Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice.

| Slice | Owns | Scope |
| --- | --- | --- |
| 1. State + Graph | `agents/state.py, agents/graph.py, agents/nodes/supervisor.py, agents/nodes/aggregator.py` | Define TenderState TypedDict. Build and compile the StateGraph with all edges. Implement Supervisor node (profile fetch + chunk validation) and Aggregator node (real merge logic). All specialist nodes (risk_radar, scorer, financial, report_assembler) are stubs that write placeholder data to state and return immediately. |
| 2. API Endpoints | `routers/tenders.py` (add analyse + status endpoints), `db/models.py` (analysis_runs) | Add analysis_runs table via Alembic migration. Implement POST /tenders/{id}/analyse (validation + background task launch) and GET /tenders/{id}/status (state polling). Wire the graph.astream() call into the background task. |
| 3. Cost Tracker | `middleware/cost_tracker.py, db/models.py` (llm_cost_events) | Implement CostTrackingHandler. Add llm_cost_events table via Alembic migration. Wire handler into the graph background task. No real LLM calls in REQ-003 — verify handler is attached and fires correctly using a mock LLM response in tests. |
| 4. Frontend | `app/upload/page.tsx` (update), `components/AgentStreamViewer.tsx` | After upload completes, automatically trigger POST /analyse and redirect to a status page. Build AgentStreamViewer component that polls GET /tenders/{id}/status every 2 seconds and shows which node is currently running (based on agent_trace in analysis_runs). WebSocket streaming comes in REQ-009 — polling is sufficient here. |
| 5. QA | `tests/test_analysis_run.py` | Test cases: valid analyse trigger, duplicate run rejection, wrong-company 403, tender-not-ready 409, no-profile failure path, empty-chunks failure path, status polling reflects state transitions, checkpoint survives mock restart, cost tracker fires on mock LLM call. |

## Slice Activation Rule

* The project owner selects which slice is executed and when — this decision is never delegated to the AI agent.
* Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope.
* The agent must not expand scope to cover other slices, and must not select the next slice on its own.

## Acceptance Criteria / Definition of Done

* [ ] POST /tenders/{id}/analyse returns HTTP 202 with run_id in under 500ms for a ready tender.
* [ ] GET /tenders/{id}/status returns the correct state at each transition: pending → running → awaiting_hitl.
* [ ] The graph runs end-to-end with stub nodes and reaches "awaiting_hitl" state without errors.
* [ ] analysis_runs.agent_trace contains an entry for every node that ran (supervisor, risk_radar, scorer, financial, aggregator).
* [ ] Triggering analyse on a tender that belongs to a different company returns HTTP 403.
* [ ] Triggering analyse on a tender with status != "ready" returns HTTP 409.
* [ ] Triggering analyse when no company profile exists causes the graph to terminate with state = "failed" and a descriptive error message.
* [ ] A run in "awaiting_hitl" state survives a simulated server restart (process kill + restart) and is still queryable via GET /status.
* [ ] CostTrackingHandler is wired correctly — fires on_llm_end when a mock LLM response is injected, and writes one llm_cost_events row with correct node_name.
* [ ] Frontend status poller shows node-level progress updating every 2 seconds during an active run.

## Document Control

This REQ is the contract for implementation. Stub node output schemas are final — do not change them when implementing REQ-004/005/006, only replace the stub logic with real LLM calls.


