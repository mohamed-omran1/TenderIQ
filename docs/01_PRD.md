
# TenderIQ — Product Requirements Document (PRD)

| Attribute | Details |
| --- | --- |
| **Document Type** | Product Requirements Document (PRD) |
| **Version** | v1.0 — Initial Release |
| **Status** | DRAFT |
| **Target Market** | Egypt & GCC — Construction, Procurement, Supply |
| **Tech Stack** | FastAPI · LangGraph · LangChain · pgvector · Next.js / TypeScript |
| **Primary Language** | English UI · Arabic + English document processing |
| **Date** | June 2026 |

---

## 1. Executive Summary

Construction and procurement firms across Egypt and the GCC spend tens of thousands of hours per year manually reading dense tender documents — evaluating FIDIC clauses, penalty conditions, letter of guarantee requirements, and financial commitments before deciding whether to bid.

TenderIQ automates this process. An analyst uploads a tender PDF and within minutes receives a structured Go/No-Go brief: a feasibility score benchmarked against the company's profile, an extracted Risk Radar highlighting dangerous clauses, and a financial commitment summary — all powered by a multi-agent LangGraph pipeline with a human-in-the-loop override gate.

The platform targets B2B SaaS monetisation in the GCC, with a per-document credit model that aligns cost directly with value delivered.

---

## 2. Problem Statement

### 2.1 Current Pain Points

* **Time-Consuming Reviews:** Tender documents range from 50 to 500+ pages, requiring 4–20 hours of manual review per document.
* **Hidden Legal Risks:** Risk clauses — especially FIDIC penalty clauses and LG bond conditions — are buried in legal language that non-legal teams miss.
* **Subjective Evaluation:** Feasibility assessment is subjective and inconsistent across analysts, leading to costly mis-bids.
* **No Historical Benchmarking:** No institutional memory: each new tender is reviewed from scratch with no benchmarking against past projects.
* **Language Barriers:** Bilingual documents (Arabic + English) add further friction for teams that are not fluent in both.

### 2.2 Who Feels This Pain

| Persona | Role | Core Pain |
| --- | --- | --- |
| **Bid Manager** | Decides go/no-go | No fast, reliable feasibility signal |
| **Contracts Engineer** | Reviews legal clauses | Misses penalty clauses under time pressure |
| **CFO / Finance Lead** | Approves financial exposure | No consolidated bond/commitment summary |
| **CEO / Owner (SMB)** | Final bid approval | Relies entirely on team's manual review |

---

## 3. Goals & Success Metrics

### 3.1 Product Goals

* Reduce tender review time from hours to **under 10 minutes** per document.
* Surface all high-risk clauses with severity scores and plain-English explanations.
* Provide a reproducible, explainable feasibility score benchmarked against a stored company profile.
* Track LLM cost per document to enable sustainable, profitable per-credit pricing.
* Support bilingual (Arabic + English) tender documents without requiring the user to pre-process them.

### 3.2 Success Metrics — MVP (Month 3)

| Metric | Target | Measurement |
| --- | --- | --- |
| **End-to-end analysis time** | < 3 minutes | p95 latency log |
| **Risk clause recall (on test set)** | > 85% | `/eval/run` endpoint |
| **Cost per analysis** | < $0.15 USD | `llm_cost_events` table |
| **Pilot customer activations** | 3 companies | CRM / usage DB |
| **Analyst override rate** | < 20% | `hitl_overrides` table |

---

## 4. Scope

### 4.1 In Scope — MVP

* PDF tender upload and multi-language chunking pipeline.
* LangGraph multi-agent analysis: Risk Radar, Feasibility Scorer, Financial Analyst.
* Human-in-the-loop override gate before report finalisation.
* Structured Go/No-Go report with risk findings table.
* LLM cost tracking per document (token usage dashboard).
* Company profile management (benchmarking data).
* WebSocket streaming of agent progress to the frontend.
* Per-tenant API key authentication.

### 4.2 Out of Scope — MVP (Planned for v2)

| Feature | Rationale for Deferral |
| --- | --- |
| **Arabic UI / RTL layout** | Adds i18n complexity; English UI sufficient for GCC enterprise users. |
| **Team collaboration / multi-user** | Single analyst workflow sufficient for MVP validation. |
| **Automated bid document drafting** | Analysis must be validated before drafting is trusted. |
| **ERP / procurement system integrations** | Requires enterprise contracts; post-revenue feature. |
| **Mobile native apps** | Responsive web sufficient for MVP. |

---

## 5. Agent Architecture

### 5.1 LangGraph State Machine

TenderIQ's analysis pipeline is implemented as a LangGraph StateGraph. All nodes read from and write to a shared `TenderState TypedDict`, enabling full auditability of agent decisions and seamless human-in-the-loop interruption.

### 5.2 Agent Nodes

| Node | Type | Responsibility |
| --- | --- | --- |
| **Ingestor** | Data | Loads PDF, detects language per page, chunks text, generates multilingual embeddings, stores to `pgvector`. |
| **Supervisor** | Orchestrator | Dispatches sub-agents in parallel (Risk Radar, Scorer, Financial Analyst) and aggregates their outputs. |
| **Risk Radar** | AI Agent | Extracts and classifies risk clauses: FIDIC conditions, penalty clauses, LG/bond requirements, termination rights. Returns structured JSON with severity scores. |
| **Feasibility Scorer** | AI Agent | Retrieves company profile and scores the tender across five dimensions: technical fit, financial capacity, timeline, geographic scope, and past experience. Returns 0–100 score with dimension breakdown. |
| **Financial Analyst** | AI Agent | Extracts all financial commitments: performance bonds, advance payment guarantees, retention amounts, liquidated damages caps, and payment schedule. |
| **HITL Gate** | Human Gate | Pauses graph execution using LangGraph `interrupt_before`. Analyst can review, adjust feasibility score, flag additional risks, or approve as-is before report assembly begins. |
| **Report Assembler** | AI Agent | Synthesises all agent outputs into a structured Markdown brief with an executive summary, Go/No-Go recommendation, risk table, financial summary, and analyst notes. |

### 5.3 Shared State Schema

All agents communicate via a single `TenderState TypedDict` persisted in the LangGraph checkpoint store:

```python
class TenderState(TypedDict):
    tender_id: str
    run_id: str
    chunks: list[dict]              # text + language + embedding_id
    risk_findings: list[RiskFinding]
    feasibility_score: float | None
    feasibility_breakdown: dict | None
    financial_summary: dict | None
    hitl_approved: bool             # False until analyst confirms
    hitl_override_score: float | None
    final_report: str | None
    token_usage: list[dict]         # accumulates cost events per node
    source_languages: list[str]     # ["ar", "en"] detected in PDF

```

*(Code snippet transferred from)*

---

## 6. API Design

### 6.1 Authentication

Every request must include an `Authorization: Bearer <api_key>` header. API keys are stored as bcrypt-hashed secrets in the `companies` table. Per-tenant rate limiting is enforced via a Redis sliding window counter — 100 analyses per day on the free tier, unlimited on paid plans.

### 6.2 Endpoints

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| **POST** | `/tenders/upload` | API Key | Accepts PDF (multipart). Stores file, triggers ingestor node, returns `tender_id`. |
| **POST** | `/tenders/{id}/analyse` | API Key | Launches full LangGraph run. Returns `run_id` immediately (async). |
| **WS** | `/tenders/{id}/stream` | API Key | WebSocket. Streams node completion events and token deltas to the UI in real time. |
| **GET** | `/tenders/{id}/report` | API Key | Returns the final structured report JSON once the run completes. |
| **POST** | `/tenders/{id}/override` | API Key | Submits analyst override (adjusted score + justification). Resumes paused graph. |
| **GET** | `/company-profile` | API Key | Returns the company's benchmarking profile used by the Feasibility Scorer. |
| **PUT** | `/company-profile` | API Key | Updates company profile fields (specialisations, financial capacity, past projects). |
| **GET** | `/analytics/cost` | API Key | Returns cost breakdown per tender and rolling monthly LLM spend. |
| **POST** | `/eval/run` | Admin | Runs the analysis against a labelled test tender and returns precision/recall per risk category. |

---

## 7. Database Schema

PostgreSQL with the `pgvector` extension. No separate vector database is required at MVP scale. All tables use UUID primary keys.

| Table | Key Columns | Notes |
| --- | --- | --- |
| **companies** | `id, api_key_hash` | One row per tenant. Stores hashed API key and rate limit tier. |
| **company_profiles** | `company_id (FK)` | JSONB columns for specialisations, financial_capacity, past_projects, max_project_value. |
| **tenders** | `id, status, primary_language` | status: uploading | processing | ready | failed. primary_language: ar | en | bilingual. |
| **tender_chunks** | `chunk_index, detected_language, embedding (vector)` | Uses pgvector's HNSW index on the embedding column for fast ANN retrieval. |
| **analysis_runs** | `state, feasibility_score, agent_trace (JSONB)` | state: pending | running | awaiting_hitl | complete | failed. agent_trace records all node outputs. |
| **risk_findings** | `category, severity, clause_text` | severity: critical | high | medium | low. category: fidic | penalty | lg_bond | termination | other. |
| **llm_cost_events** | `node_name, model, input_tokens, cost_usd` | Written by FastAPI callback middleware on every `on_llm_end` event. Enables per-document cost analytics. |
| **hitl_overrides** | `original_score, overridden_score, justification` | Immutable audit log. One row per override. Used to measure analyst agreement rate with AI. |

---

## 8. Frontend — Next.js / TypeScript

### 8.1 Pages & Key Components

| Route | Component | Description |
| --- | --- | --- |
| `/` | Dashboard | List of tenders with status badges and quick Go/No-Go scores. |
| `/upload` | TenderUpload | Drag-and-drop PDF upload. Triggers analysis and redirects to stream view. |
| `/tenders/[id]` | AgentStreamViewer | Live WebSocket feed showing each agent node completing in real time. |
| `/tenders/[id]/report` | ReportViewer + RiskRadarTable + HITLGate | Full structured report. Analyst can override score and approve before the report is finalised. |
| `/analytics` | CostDashboard | Cost-per-tender chart and monthly LLM burn rate. Powered by `/analytics/cost` endpoint. |
| `/profile` | CompanyProfileForm | Edit the company benchmarking profile used by the Feasibility Scorer. |

---

## 9. Build Plan — 4-Week MVP

### **Week 1: Foundation**

* **Deliverables:** Docker Compose (Postgres + pgvector + Redis), FastAPI skeleton with API key auth + rate limiting middleware, Alembic migrations for full schema, PDF upload endpoint + Ingestor node (chunk, embed, store).
* **Checkpoint:** Can upload a tender and query chunks via API.

### **Week 2: Core Agents**

* **Deliverables:** LangGraph StateGraph wired: Supervisor, Risk Radar, Feasibility Scorer, Financial Analyst nodes. Structured output schemas via Pydantic. Polling endpoint for run status. LLM cost tracker middleware writing to `llm_cost_events`.
* **Checkpoint:** Full analysis pipeline testable via Postman with real tender PDF.

### **Week 3: HITL + Streaming**

* **Deliverables:** Human-in-the-loop gate using LangGraph `interrupt_before` on Report Assembler. Override endpoint resumes graph. WebSocket streaming endpoint broadcasting agent events. `/eval/run` evaluation harness.
* **Checkpoint:** Complete analyst workflow functional end-to-end.

### **Week 4: Frontend + Launch**

* **Deliverables:** Next.js pages: Dashboard, Upload, AgentStreamViewer, ReportViewer with HITL gate, CostDashboard. Deploy to Railway (backend) + Vercel (frontend). Write README with LangGraph state diagram. Record 3-minute Loom demo.
* **Checkpoint:** Shareable public URL for pilot clients.

---

## 10. Monetisation Model

### 10.1 Pricing Tiers

| Tier | Price | Credits / mo | Target |
| --- | --- | --- | --- |
| **Starter** | Free | 5 analyses | Individual consultants, validation |
| **Growth** | $99 / month | 50 analyses | SMB contractors, Egypt market |
| **Business** | $299 / month | 200 analyses | Mid-size firms, GCC market |
| **Enterprise** | Custom | Unlimited | Large contractors, custom SLA + integrations |

At a target cost of $0.10–$0.15 per analysis (LLM + infrastructure), the Growth tier yields approximately 60–70% gross margin, consistent with sustainable B2B SaaS unit economics.

---

## 11. Risks & Mitigations

| Severity | Risk | Likelihood | Mitigation |
| --- | --- | --- | --- |
| **HIGH** | LLM hallucination on risk clause extraction leads to missed penalties. | Medium | Mandatory `/eval/run` harness; HITL gate before any report is finalised; confidence scores on each finding. |
| **MED** | LLM cost per document exceeds pricing model. | Low–Med | `llm_cost_events` tracking from day one. Use caching for repeated clause patterns. Prompt compression. |
| **MED** | Arabic OCR quality degrades for scanned tenders. | Medium | Use GPT-4o vision API for scanned pages. Flag low-confidence chunks to analyst for manual review. |
| **LOW** | Scope creep delays MVP beyond 4 weeks. | Low | v2 features list maintained. PRD is the contract. Weekly milestone checkpoints enforced. |

---

## 12. Open Questions

1. **Sample tender PDF:** A real or public-domain bilingual tender document is needed to calibrate Risk Radar prompts. Target: before Week 2 begins.
2. **Company profile schema:** Finalise the exact fields for specialisations and `financial_capacity` JSONB columns with a real-world contractor profile.
3. **Report output priorities:** Confirm with first pilot client which of the three report sections (Go/No-Go score, Risk Radar, Financial Summary) is most critical to render first.
4. **Embedding model selection:** Confirm whether `text-embedding-3-large` (OpenAI) or `multilingual-e5-large` (open source, self-hosted) is preferred for cost vs. quality tradeoff.
5. **Deployment target:** Railway + Vercel assumed. Confirm whether client data residency requirements (e.g. Saudi Arabia cloud regulations) mandate a specific cloud region.

---

## Document Control

This PRD is a living document. All scope changes must be reviewed against the Week 1–4 build plan and the Out of Scope list before being accepted into the MVP. Changes that push the launch beyond Week 4 are automatically deferred to v2.

