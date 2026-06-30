Read the following documents before writing any code:
- docs/reqs/REQ-003_LangGraph_Analysis_Run.md
- docs/02_Architecture.md (section3— LangGraph Implementation Detail)  

You are implementing **REQ-003 — Slice 1 (State + Graph Skeleton) only**.

REQ-001 and REQ-002 are complete. The following are available:
- app/agents/tools/profile_lookup.py → async LangChain tool
- app/db/models.py → CompanyProfile, Tender, TenderChunk models
- app/schemas/company.py → CompanyProfileSchema

---

## Your scope (do not touch anything outside this list)
- app/agents/state.py (create)
- app/agents/graph.py (create)
- app/agents/nodes/supervisor.py (create)
- app/agents/nodes/aggregator.py (create)
- app/agents/nodes/risk_radar.py (create — stub only)
- app/agents/nodes/feasibility_scorer.py (create — stub only)
- app/agents/nodes/financial_analyst.py (create — stub only)
- app/agents/nodes/report_assembler.py (create — stub only)
- app/agents/__init__.py (update exports if needed)

---

## What to implement

### 1. app/agents/state.py
Define TenderState as a TypedDict with exactly these fields
(no additions, no removals — this schema is final):

  tender_id:             str
  run_id:                str
  company_id:            str
  chunks:                list[dict]        # {content, detected_language, chunk_index}
  supervisor_ready:      bool
  risk_findings:         list[dict]
  feasibility_score:     float | None
  feasibility_breakdown: dict | None
  financial_summary:     dict | None
  aggregated_results:    dict | None
  hitl_approved:         bool
  hitl_override_score:   float | None
  final_report:          str | None
  token_usage:           list[dict]
  source_languages:      list[str]

### 2. app/agents/nodes/supervisor.py
Real logic (not a stub):

async def supervisor_node(state: TenderState, config: RunnableConfig) -> TenderState:
  - Extract company_id and tender_id from state
  - Call profile_lookup tool with company_id
    - If ValueError raised: set state["supervisor_ready"] = False
      and raise a GraphInterruptException with message
      "No company profile found." so the graph terminates cleanly
  - Check state["chunks"] is non-empty
    - If empty: raise GraphInterruptException
      "No content chunks found for tender."
  - Set state["supervisor_ready"] = True
  - Detect unique languages from chunks:
    state["source_languages"] = list of unique detected_language
    values across all chunks
  - Return updated state
  - Do NOT call any LLM in this node

### 3. Stub nodes — risk_radar, feasibility_scorer,
   financial_analyst, report_assembler

Each stub must:
  a) Be a proper async function matching the node signature:
     async def <name>_node(state: TenderState, config: RunnableConfig) -> TenderState
  b) Write EXACTLY these placeholder values to state
     (schema is final — do not change field names or structure):

     risk_radar:
       state["risk_findings"] = [{"category": "stub", "severity": "low",
         "clause_text": "STUB", "explanation": "Stub — REQ-004 pending"}]

     feasibility_scorer:
       state["feasibility_score"] = 0.0
       state["feasibility_breakdown"] = {"stub": True}

     financial_analyst:
       state["financial_summary"] = {"stub": True, "bonds": [],
         "commitments": []}

     report_assembler:
       state["final_report"] = "STUB REPORT — REQ-008 pending"

  c) Log a single INFO line: f"[STUB] {node_name} executed for run {state['run_id']}"
     (this confirms the node ran without polluting logs with sensitive data)
  d) Return updated state

### 4. app/agents/nodes/aggregator.py
Real logic (not a stub):

async def results_aggregator_node(state: TenderState, config: RunnableConfig) -> TenderState:
  - Merge all three specialist outputs into one dict:
    state["aggregated_results"] = {
      "risk_findings": state["risk_findings"],
      "feasibility_score": state["feasibility_score"],
      "feasibility_breakdown": state["feasibility_breakdown"],
      "financial_summary": state["financial_summary"],
      "source_languages": state["source_languages"],
    }
  - Return updated state
  - Do NOT call any LLM in this node

### 5. app/agents/graph.py
Build and compile the graph:

  from langgraph.graph import StateGraph, END
  from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

  builder = StateGraph(TenderState)

  # Add all nodes
  builder.add_node("supervisor",        supervisor_node)
  builder.add_node("risk_radar",        risk_radar_node)
  builder.add_node("scorer",            feasibility_scorer_node)
  builder.add_node("financial",         financial_analyst_node)
  builder.add_node("aggregator",        results_aggregator_node)
  builder.add_node("report_assembler",  report_assembler_node)

  # Entry point
  builder.set_entry_point("supervisor")

  # Fan-out: supervisor → 3 parallel branches
  builder.add_edge("supervisor", "risk_radar")
  builder.add_edge("supervisor", "scorer")
  builder.add_edge("supervisor", "financial")

  # Fan-in: all 3 → aggregator
  builder.add_edge(["risk_radar", "scorer", "financial"], "aggregator")

  # Aggregator → report assembler (HITL gate sits here)
  builder.add_edge("aggregator", "report_assembler")
  builder.add_edge("report_assembler", END)

  # Compile with Postgres checkpointer + HITL interrupt
  graph = builder.compile(
      checkpointer=AsyncPostgresSaver.from_conn_string(DATABASE_URL),
      interrupt_before=["report_assembler"],
  )

The compiled graph object must be importable as:
  from app.agents.graph import graph

---

## Dependency versions to use
Use Context7 to confirm the exact current API for these before writing:
- langgraph (AsyncPostgresSaver import path, add_edge fan-in syntax,
  interrupt_before parameter name)
- langchain-core (RunnableConfig import path)

Do NOT rely on training memory for LangGraph API — it changes
frequently. Fetch the docs first.

---

## Rules
- Do NOT implement any LLM calls in any node in this slice.
- Do NOT create any router, migration, or frontend files.
- Do NOT create test files — that is Slice 5.
- Do NOT add any new pip packages without listing them explicitly
  in your summary so I can verify they are already in requirements.txt.
- TenderState fields are final — do not add, remove, or rename any field.
- The graph object must be compiled at module import time
  (not inside a function) so it is reused across all requests.
- Use AsyncPostgresSaver (async version) — not the sync PostgresSaver.
- DATABASE_URL must be read from environment variable — never hardcoded.

---

## When you finish
Show me:
1. Full file tree of everything you created (8 files)
2. Confirm the graph compiles without error by running:
   python -c "from app.agents.graph import graph; print('Graph OK')"
3. Confirm fan-out is wired correctly — show me the
   graph.get_graph().edges output so I can verify the 3 parallel edges
4. Confirm interrupt_before=["report_assembler"] is set
5. List any new pip packages you used that need to be added
   to requirements.txt

Do not move to Slice 2 until I explicitly tell you to.