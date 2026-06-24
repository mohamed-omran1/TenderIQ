## Part 2: System Architecture Document

| Attribute | Details |
| --- | --- |
| **Document Type** | System Architecture Document (companion to PRD v1.0) |
| **Version** | v1.0 — Initial Release |
| **Status** | DRAFT |
| **Audience** | Engineering — implementation reference for Weeks 1–4 |
| **Tech Stack** | FastAPI · LangGraph · LangChain · PostgreSQL/pgvector · Redis · Next.js |
| **Related Docs** | TenderIQ_PRD_v1.0.docx |
| **Date** | June 2026 |

---

### 1. Architecture Overview

TenderIQ is a request-driven, asynchronously-processed multi-agent system. A client uploads a PDF, which triggers a background LangGraph run orchestrated by FastAPI's task system.

Progress streams to the frontend over WebSocket. All state is checkpointed in PostgreSQL, enabling the graph to pause indefinitely at the human-in-the-loop gate without holding server resources.

The system is intentionally a modular monolith for MVP — a single FastAPI service handling API, orchestration, and WebSocket concerns — rather than separate microservices. This avoids premature distributed-systems complexity while keeping clear internal boundaries (routers / agents / db / middleware) that allow clean extraction into services later if scale demands it.

#### 1.1 High-Level Component Diagram

* **Next.js Frontend:** Dashboard, Upload, Stream Viewer, Report Viewer, HITL Gate, Cost Analytics.
* **FastAPI Backend:** REST routers, WebSocket gateway, auth + rate-limit middleware, cost-tracking callback handler.
* **LangGraph Runtime:** Supervisor + 5 agent nodes, checkpointer, interrupt_before HITL gate.
* **PostgreSQL + pgvector:** Relational data, vector embeddings, LangGraph checkpoint store, cost event log.
* **Redis:** Rate-limit counters (sliding window), WebSocket pub/sub for multi-worker broadcast.

Data flows in one direction for a single analysis: Frontend → FastAPI → LangGraph → Postgres, with WebSocket events flowing back out of LangGraph (via callback) through FastAPI to the Frontend at every node transition.

---

### 2. Request Lifecycle — End to End

This section traces a single tender analysis from upload to final report, matching the sequence implemented across Weeks 1–3 of the build plan.

1. **Upload:** Client calls `POST /tenders/upload` with a multipart PDF. FastAPI validates file type and size, stores the raw file (local disk for MVP, S3-compatible bucket for production), and inserts a tenders row with `status = "uploading"`.
2. **Ingestion:** FastAPI immediately returns `tender_id` (HTTP 202) and dispatches the Ingestor node as a background task via FastAPI's `BackgroundTasks` (or a Celery/RQ worker once volume justifies it).
3. **Processing:** The Ingestor node extracts text per page, runs language detection per chunk (Arabic / English / mixed), generates embeddings, and writes rows to `tender_chunks` with pgvector's HNSW index. `tenders.status` moves to `"ready"`.
4. **Analysis Trigger:** Client calls `POST /tenders/{id}/analyse`. FastAPI creates an `analysis_runs` row (`state = "running"`) and invokes the compiled LangGraph graph asynchronously, passing a fresh `TenderState`.
5. **Streaming:** The client opens a WebSocket connection to `/tenders/{id}/stream`. A LangChain callback handler attached to the graph emits a message over this socket on every node entry/exit and on every token from streaming-enabled nodes.
6. **Parallel Branching:** The Supervisor node fans out to Risk Radar, Feasibility Scorer, and Financial Analyst concurrently using LangGraph's parallel branching (a fan-out/fan-in pattern), then merges their outputs into shared state.
7. **Cost Logging:** Every LLM call across every node passes through the cost-tracking callback, which writes one row per call to `llm_cost_events` with token counts and computed USD cost.
8. **HITL Gate Interrupt:** The graph reaches the HITL gate, compiled with `interrupt_before=["report_assembler"]`. Execution pauses; `analysis_runs.state` becomes `"awaiting_hitl"`. The checkpoint is persisted to Postgres so the process can pause indefinitely — no server thread is blocked.
9. **Override / Approval:** The analyst reviews the findings in the frontend and either approves as-is or calls `POST /tenders/{id}/override` with an adjusted score and justification, written to `hitl_overrides`.
10. **Resume & Assemble:** FastAPI resumes the graph from its checkpoint. The Report Assembler node runs, producing the final Markdown/JSON report. `analysis_runs.state` becomes `"complete"`.
11. **Retrieval:** Client calls `GET /tenders/{id}/report` to retrieve the final structured output, or simply receives it as the last WebSocket event.

---

### 3. LangGraph Implementation Detail

#### 3.1 Graph Construction

The graph is built once at application startup and compiled with a PostgreSQL-backed checkpointer, so paused runs survive a server restart — important for a gate that may sit open for hours while an analyst is unavailable.

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(DATABASE_URL)
builder = StateGraph(TenderState)

builder.add_node("ingestor", ingestor_node)
builder.add_node("supervisor", supervisor_node)
builder.add_node("risk_radar", risk_radar_node)
builder.add_node("scorer", feasibility_scorer_node)
builder.add_node("financial", financial_analyst_node)
builder.add_node("aggregator", results_aggregator_node)
builder.add_node("report_assembler", report_assembler_node)

builder.set_entry_point("supervisor")
builder.add_edge("supervisor", "risk_radar")
builder.add_edge("supervisor", "scorer")
builder.add_edge("supervisor", "financial")
builder.add_edge(["risk_radar", "scorer", "financial"], "aggregator")
builder.add_edge("aggregator", "report_assembler")
builder.add_edge("report_assembler", END)

graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["report_assembler"],
)

```

#### 3.2 Fan-out / Fan-in Pattern

Risk Radar, Feasibility Scorer, and Financial Analyst have no dependency on each other's output, so they run as parallel branches from the Supervisor node. LangGraph executes all three concurrently and waits for all to complete before the Aggregator node runs — this alone cuts wall-clock latency by roughly 60% versus sequential execution, since each agent call is dominated by LLM I/O wait time.

#### 3.3 Resuming After the HITL Gate

Resuming is a thread-id-scoped operation: the same checkpoint `thread_id` used for the initial run is passed back in when the analyst approves or overrides, and LangGraph reconstructs `TenderState` exactly where it left off.

```python
# On analyst approval/override:
config = {"configurable": {"thread_id": str(run_id)}}

if override_score is not None:
    graph.update_state(
        config,
        {"hitl_override_score": override_score, "hitl_approved": True}
    )
else:
    graph.update_state(config, {"hitl_approved": True})

async for event in graph.astream(None, config):  # None = resume
    await broadcast_to_websocket(run_id, event)

```

---

### 4. Cost Tracking Middleware

A custom LangChain callback handler is attached to every node's LLM client, intercepting `on_llm_end` events regardless of which node triggered them.

```python
class CostTrackingHandler(BaseCallbackHandler):
    def __init__(self, run_id: str, node_name: str):
        self.run_id = run_id
        self.node_name = node_name

    async def on_llm_end(self, response, **kwargs):
        usage = response.llm_output.get("usage", {})
        cost = compute_cost(
            model=response.llm_output["model_name"],
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],     
        )
        await db.execute(insert(llm_cost_events).values(
            run_id=self.run_id, node_name=self.node_name,
            model=response.llm_output["model_name"],
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            cost_usd=cost,
        ))

```

Because the handler is parameterised by `node_name`, the `/analytics/cost` endpoint can break down spend by agent — surfacing, for example, that Risk Radar consumes 3x the tokens of the Feasibility Scorer, which directly informs prompt-optimisation priorities.

---

### 5. Deployment Topology

#### 5.1 MVP Deployment (Weeks 1–4)

| Component | Platform | Notes |
| --- | --- | --- |
| **FastAPI backend** | Railway | Single container, autoscale disabled at MVP; vertical scaling only. |
| **Next.js frontend** | Vercel | Edge-deployed, connects to Railway backend via public HTTPS + WSS. |
| **PostgreSQL + pgvector** | Railway / Supabase | Managed instance; pgvector extension enabled via migration. |
| **Redis** | Railway / Upstash | Rate-limit counters + WebSocket pub/sub for horizontal scale readiness. |
| **File storage** | Local volume → Cloudflare R2 | Start local; migrate to R2 before first paid pilot to avoid data loss on redeploy. |

#### 5.2 Environments

* **local:** Docker Compose running Postgres + pgvector + Redis; FastAPI run with `--reload`.
* **staging:** Mirrors production topology, used for pilot client demos and `/eval/run` regression checks before each deploy.
* **production:** Same topology, environment-isolated database, stricter rate limits removed for paid tenants.

#### 5.3 Data Residency Note

If a GCC enterprise pilot requires in-region data residency (notably Saudi Arabia), the managed Postgres instance and file storage bucket should be re-provisioned in a compliant region (e.g. AWS `me-south-1`) ahead of that engagement. This is tracked as an open question in the PRD and does not block MVP launch.

---

### 6. Security Model

#### 6.1 Authentication & Authorization

Every API request requires `Authorization: Bearer <api_key>`. Keys are generated server-side, shown once, and stored only as a bcrypt hash in `companies.api_key_hash`.

FastAPI dependency `get_current_company` resolves the key on every request and injects the tenant context — all DB queries are scoped to `company_id`, preventing cross-tenant data access.

WebSocket connections authenticate via a short-lived signed token issued by a REST call immediately before the socket upgrade, since browsers cannot set custom headers on WS connections.

#### 6.2 Rate Limiting

A Redis sliding-window counter, keyed by `company_id`, enforces tier limits at the FastAPI middleware layer before any request reaches a router. Exceeding the limit returns HTTP 429 with a `Retry-After` header.

#### 6.3 Data Protection

Tender PDFs may contain commercially sensitive data — encrypted at rest (provider-managed encryption on Railway/R2) and in transit (TLS everywhere).

`hitl_overrides` and `analysis_runs.agent_trace` are immutable audit logs — updates are append-only, never destructive, supporting enterprise audit requirements. No tender content is used for model fine-tuning or shared across tenants under any circumstance.

---

### 7. Observability

| Concern | Mechanism | Purpose |
| --- | --- | --- |
| **LLM cost** | `llm_cost_events` table + `/analytics/cost` | Per-document and monthly spend visibility, margin protection. |
| **Agent accuracy** | `/eval/run` against labelled test tenders | Tracks precision/recall drift as prompts evolve. |
| **Run state** | `analysis_runs.state` + `agent_trace` JSONB | Full replay of what each node decided, for debugging and support. |
| **Analyst trust** | `hitl_overrides` override-rate query | High override rate signals the scorer needs retuning. |
| **App errors** | Structured logging (JSON) → stdout | MVP-level; upgrade to Sentry once first paid pilot is live. |

---

### 8. Scaling Considerations — Post-MVP

| Trigger | Response |
| --- | --- |
| **Background tasks block API responsiveness** | Move from FastAPI BackgroundTasks to a dedicated worker queue (Celery + Redis or RQ), decoupling ingestion/analysis from the request-response cycle entirely. |
| **Single Postgres instance becomes a bottleneck** | Read replicas for analytics queries; consider a managed vector DB (Pinecone/Qdrant) only if pgvector HNSW query latency measurably degrades at scale. |
| **WebSocket fan-out across multiple server instances** | Redis pub/sub already in place from MVP — each instance subscribes to a run-scoped channel, enabling horizontal scaling without code changes. |
| **Need for true microservice isolation** | Extract the LangGraph runtime into its own service behind an internal API once agent logic and API logic have genuinely independent release cadences — not before. |

---

### 9. Traceability to PRD

This document implements PRD v1.0 sections 5 (Agent Architecture), 6 (API Design), and 7 (Database Schema) at an engineering level of detail. Any change to the agent node list, endpoint contracts, or schema must be reflected in both documents to keep them in sync.

---

### Document Control

This is a living document. Update alongside the PRD whenever architecture decisions change during implementation.

