# TenderIQ
### AI-Powered Tender Analysis Platform for Egypt & GCC B2B Markets

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph)
[![Next.js](https://img.shields.io/badge/Next.js-14+-000000?style=flat-square&logo=nextdotjs&logoColor=white)](https://nextjs.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-MVP%20Active%20Development-blue?style=flat-square)]()

---

## What is TenderIQ?

Construction and procurement firms across Egypt and the GCC spend tens of thousands of hours per year reading dense tender documents — evaluating FIDIC clauses, penalty conditions, letter-of-guarantee requirements, and financial commitments before deciding whether to bid.

**TenderIQ automates this.** An analyst uploads a tender PDF and within minutes receives a structured Go/No-Go brief: a feasibility score benchmarked against the company's profile, an extracted Risk Radar highlighting dangerous clauses, and a financial commitment summary — all powered by a multi-agent LangGraph pipeline with a human-in-the-loop override gate.

The platform supports **bilingual Arabic + English** tender documents natively, with Arabic clause text preserved verbatim and explanations always produced in English.

---

## Architecture

```
PDF Upload
    │
    ▼
┌─────────────┐
│  Ingestor   │ → chunk + embed → pgvector (AR/EN detection per chunk)
└─────────────┘
    │
    ▼
┌─────────────┐
│  Supervisor │ → validates company profile + chunk availability
└─────────────┘
    │
┌───┴───────────────────┐
│           │           │
▼           ▼           ▼
Risk      Feasibility  Financial
Radar     Scorer       Analyst
(REQ-004) (REQ-005)   (REQ-006)
    │           │           │
    └─────┬─────────────────┘
          │
          ▼
    ┌─────────────┐
    │ Aggregator  │ → merges all three outputs
    └─────────────┘
          │
     [HITL Gate] ← analyst reviews, can override score
          │
          ▼
    ┌──────────────────┐
    │ Report Assembler │ → structured Go/No-Go brief
    └──────────────────┘
```

**Key architectural decisions:**

- **Parallel fan-out** — Risk Radar, Feasibility Scorer, and Financial Analyst run concurrently, not sequentially. This cuts wall-clock latency by ~60%.
- **Persistent HITL gate** — `interrupt_before=["report_assembler"]` with Postgres checkpointing. The graph survives server restarts while awaiting analyst approval.
- **Python-side determinism** — Feasibility composite score computed in Python (sum of 5 LLM-scored dimensions). Go/No-Go thresholds enforced in Python. The LLM synthesises, never decides.
- **Real-time streaming** — Redis pub/sub fan-out delivers WebSocket events to all connected clients as nodes complete.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | FastAPI (async) · SQLAlchemy · Alembic |
| **AI Orchestration** | LangGraph · LangChain |
| **LLM Providers** | OpenAI GPT-4o · Anthropic Claude Sonnet |
| **Vector DB** | PostgreSQL + pgvector (HNSW index) |
| **Relational DB** | PostgreSQL (same instance) |
| **Cache / Streaming** | Redis (rate limiting + WebSocket pub/sub) |
| **Frontend** | Next.js  · TypeScript |
| **UI Components** | Shadcn/ui · Tailwind CSS |
| **State Management** | TanStack Query v5 · Zustand |
| **Deployment** | Railway (backend) · Vercel (frontend) |

---

## Key Features

- **Multi-agent parallel pipeline** — Three specialist LLM agents run concurrently via LangGraph fan-out: Risk Radar (FIDIC clause extraction), Feasibility Scorer (5-dimension company profile matching), Financial Analyst (bond/LG/payment extraction).

- **Human-in-the-loop gate** — Analyst reviews AI output before report assembly. Can override the feasibility score with a written justification. Full immutable audit trail in `hitl_overrides` table.

- **Structured extraction with eval** — FIDIC clause extraction targets ≥85% recall, measured against a labelled ground-truth tender via the `/eval/run` endpoint and `eval/run_eval.py` CLI.

- **ISO 4217 currency normalisation** — Financial extraction validates and normalises currency codes post-LLM (e.g. "Riyals" → "SAR"). Ambiguous currencies flagged with `needs_review=True`.

- **LLM cost tracking per document** — `CostTrackingHandler` (LangChain callback) writes token usage per node to `llm_cost_events`. Exposed via `/analytics/cost` for margin visibility.

- **Real-time WebSocket streaming** — Redis pub/sub delivers node-level progress events to the frontend as they happen. Polling fallback activates automatically if WebSocket fails.

- **Bilingual document support** — Arabic + English tender PDFs processed natively. Language detected per chunk. Arabic clause text preserved verbatim in findings.

---

## Project Structure

```
tenderiq/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── graph.py              # compiled LangGraph StateGraph
│   │   │   ├── state.py              # TenderState TypedDict (15 fields)
│   │   │   ├── nodes/                # one file per agent node
│   │   │   │   ├── ingestor.py
│   │   │   │   ├── supervisor.py
│   │   │   │   ├── risk_radar.py
│   │   │   │   ├── feasibility_scorer.py
│   │   │   │   ├── financial_analyst.py
│   │   │   │   ├── aggregator.py
│   │   │   │   └── report_assembler.py
│   │   │   ├── skills/               # prompt packages (skill per node)
│   │   │   │   ├── risk_clause_extraction.py
│   │   │   │   ├── feasibility_scoring.py
│   │   │   │   ├── financial_extraction.py
│   │   │   │   └── report_synthesis.py
│   │   │   ├── tools/                # LangChain tools
│   │   │   │   └── profile_lookup.py
│   │   │   └── retrieval.py          # pgvector retrieval helpers
│   │   ├── api/
│   │   │   └── routers/
│   │   │       ├── tenders.py        # upload, analyse, HITL, report
│   │   │       ├── company.py        # profile CRUD
│   │   │       ├── stream.py         # WebSocket endpoint
│   │   │       ├── analytics.py      # cost tracking
│   │   │       └── eval.py           # admin eval harness
│   │   ├── db/                       # SQLAlchemy models + Alembic
│   │   ├── middleware/
│   │   │   └── cost_tracker.py       # LangChain callback → DB
│   │   ├── services/
│   │   │   └── event_bus.py          # Redis pub/sub wrapper
│   │   └── schemas/                  # Pydantic request/response models
│   ├── tests/                        # pytest suite (real DB, mock LLM)
│   └── eval/                         # accuracy evaluation scripts
│       ├── run_eval.py
│       ├── schemas.py
│       └── labelled_sample_tender.json
├── frontend/
│   ├── app/                          # Next.js App Router pages
│   │   ├── page.tsx                  # dashboard
│   │   ├── upload/page.tsx
│   │   ├── profile/page.tsx
│   │   ├── tenders/[id]/
│   │   │   ├── page.tsx              # AgentStreamViewer
│   │   │   └── report/
│   │   │       ├── page.tsx          # pre-report + HITL gate
│   │   │       └── full/page.tsx     # final Go/No-Go report
│   │   ├── analytics/page.tsx
│   │   └── eval/page.tsx             # admin eval page
│   ├── components/                   # UI components
│   ├── hooks/
│   │   └── useRunStream.ts           # WebSocket hook
│   └── lib/api/                      # typed API client functions
├── docs/
│   ├── 01_PRD.md
│   ├── 02_Architecture.md
│   ├── reqs/                         # REQ-001 through REQ-012
│   └── reports/                      # implementation reports per REQ
├── docker-compose.yml
└── README.md
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker + Docker Compose
- OpenAI API key (or compatible LLM provider)

### Local Setup

```bash
# 1. Clone the repo
git clone https://github.com/MohamedOmran/tenderiq.git
cd tenderiq

# 2. Start infrastructure (Postgres + pgvector + Redis)
docker-compose up -d

# 3. Backend setup
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your API keys
alembic upgrade head            # run all migrations
uvicorn app.main:app --reload

# 4. Frontend setup (new terminal)
cd frontend
npm install
cp .env.example .env.local      # fill in API URL and key
npm run dev

# 5. Verify
curl http://localhost:8000/health
# → {"status": "ok"}
open http://localhost:3000
```

### Environment Variables

**Backend (`backend/.env`):**

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `TEST_DATABASE_URL` | Separate DB for pytest suite |
| `REDIS_URL` | Redis connection string |
| `OPENAI_API_KEY` | LLM provider key |
| `SECRET_KEY` | For API key hashing (bcrypt) |
| `ADMIN_API_KEY` | For `/eval/run` admin endpoint |

**Frontend (`frontend/.env.local`):**

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Backend HTTP URL (e.g. `http://localhost:8000`) |
| `NEXT_PUBLIC_WS_BASE_URL` | Backend WebSocket URL (e.g. `ws://localhost:8000`) |
| `NEXT_PUBLIC_API_KEY` | Company API key for dev |
| `NEXT_PUBLIC_ADMIN_KEY` | Admin key for `/eval` page |

---

## Running Tests

```bash
cd backend

# Full test suite
pytest -v

# Specific REQ suite
pytest tests/test_risk_radar.py -v
pytest tests/test_hitl.py -v

# Skip slow tests (WebSocket heartbeat — 16s wait)
pytest -v -m "not slow"

# With coverage
pytest --cov=app --cov-report=html

# Accuracy evaluation (requires real tender + API key)
python eval/run_eval.py \
  --tender-id <uuid> \
  --company-id <uuid> \
  --risk --scorer \
  --output text
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/tenders/upload` | Upload PDF tender |
| `POST` | `/tenders/{id}/analyse` | Launch analysis pipeline |
| `GET` | `/tenders/{id}/status` | Poll run state |
| `WS` | `/tenders/{id}/stream` | Real-time agent events |
| `GET` | `/tenders/{id}/findings` | Risk clause findings |
| `GET` | `/tenders/{id}/financial` | Financial commitments |
| `GET` | `/tenders/{id}/report` | Final Go/No-Go report |
| `POST` | `/tenders/{id}/approve` | HITL approve as-is |
| `POST` | `/tenders/{id}/override` | HITL override score |
| `GET` | `/tenders/{id}/hitl-override` | HITL audit record |
| `GET` | `/company-profile` | Get company profile |
| `PUT` | `/company-profile` | Update company profile |
| `GET` | `/analytics/cost` | LLM cost per document |
| `POST` | `/eval/run` | Run accuracy evaluation (admin) |
| `GET` | `/eval/results` | Eval history (admin) |

Interactive docs: `http://localhost:8000/docs`

---

## Implementation Status

| REQ | Feature | Status |
|---|---|---|
| REQ-001 | PDF Upload & Ingestion | ✅ Complete |
| REQ-002 | Company Profile Management | ✅ Complete |
| REQ-003 | LangGraph Analysis Run | ✅ Complete |
| REQ-004 | Risk Radar Node | ✅ Real LLM |
| REQ-005 | Feasibility Scorer Node | ✅ Real LLM |
| REQ-006 | Financial Analyst Node | ✅ Real LLM |
| REQ-007 | HITL Override Gate | ✅ Complete |
| REQ-008 | Report Assembler | ✅ Real LLM |
| REQ-009 | WebSocket Streaming | ✅ Complete |
| REQ-010 | LLM Cost Tracking | ✅ (wired in REQ-003) |
| REQ-011 | API Auth + Rate Limiting | ✅ (wired in REQ-001) |
| REQ-012 | Evaluation Harness | ✅ Complete |

---

## Documentation

| Document | Location |
|---|---|
| Product Requirements Document | `docs/01_PRD.md` |
| Architecture Document | `docs/02_Architecture.md` |
| Functional Requirements (REQ-001 to REQ-012) | `docs/reqs/` |
| Implementation Reports | `docs/reports/` |
| MVP Testing Guide | `docs/TenderIQ_MVP_Testing_Guide.md` |

---

## How I Built This

**Skill-based agent design** — Each LangGraph node has a dedicated skill package (prompt + schema + few-shot examples) in `agents/skills/`, separate from the node's control flow. This means prompt iteration doesn't touch node logic, and each skill is independently reviewable.

**Slice-driven implementation** — Every feature was implemented as ordered slices (Backend → Agent → Frontend → QA), each with a defined scope and acceptance criteria before any code was written. This prevents scope creep and makes AI-assisted development auditable.

**Measurable output quality** — Non-deterministic LLM outputs have evaluation thresholds. Risk Radar targets ≥85% recall on a labelled ground-truth tender. Feasibility scoring targets ≤5.0 std dev across repeated runs. Both measured via `eval/run_eval.py`.

**Production failure modes** — Every LLM node has two distinct retry strategies: schema validation failures degrade gracefully (return empty/fallback, continue pipeline); API failures propagate after backoff (fail fast). The Report Assembler never fails a run regardless of LLM errors.

---

## License

MIT License — see [LICENSE](LICENSE) file.

---

<div align="center">
<sub>Built by Mohamed Omran · Cairo, Egypt 🇪🇬 · TenderIQ MVP 2026</sub>
</div>
