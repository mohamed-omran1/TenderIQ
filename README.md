# TenderIQ

B2B SaaS that automates tender-document analysis for construction/procurement firms in Egypt & the GCC. Upload a tender PDF → multilingual (Arabic + English) chunking + embeddings → LangGraph multi-agent pipeline (Risk Radar, Feasibility Scorer, Financial Analyst) → Go/No-Go brief with a human-in-the-loop gate.

This repository currently implements **REQ-001: PDF Upload & Ingestion** (Slices 1, 2, 4 — backend, ingestor node, tests).

## Stack
FastAPI · LangGraph · LangChain · PostgreSQL + pgvector · Redis · Gemini (`gemini-embedding-001`) · pytest

## Quick start

```bash
# 1. Infra (Postgres 16 + pgvector, Redis)
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # or: .venv\Scripts\activate (Windows)
pip install -e ".[dev]"
cp .env.example .env          # fill in GOOGLE_API_KEY from https://aistudio.google.com/apikey

# 3. Schema (creates pgvector extension, tables, HNSW index)
alembic upgrade head

# 4. Run
uvicorn app.main:app --reload --port 8000

# 5. Test (no real Gemini calls — embeddings are stubbed)
pytest -q
```

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/tenders/upload` | Multipart PDF upload. Validates type/size, stores file, kicks off ingestion. Returns `202 { tender_id, status: "uploading" }`. |
| `GET`  | `/tenders/{id}` | Tenant-scoped tender status + primary language. |

All endpoints require `Authorization: Bearer <api_key>`. Keys are bcrypt-hashed in `companies.api_key_hash`; resolve a tenant by seeding one (see `tests/conftest.py:create_company`).

## Architecture notes

- **Multi-tenant:** every business query filters by `company_id`. `tender_chunks` carries a denormalized `company_id` (deviation from PRD §7, justified by the `rag-architect` + `api-security-reviewer` skills — every vector retrieval must filter `WHERE tender_id AND company_id` without a join).
- **Embeddings:** Gemini `gemini-embedding-001`, 768 dimensions (Matryoshka-truncated from the 3072 default). The dimension is pinned in the `tender_chunks.embedding vector(768)` column — changing it is a full re-embed, not a migration.
- **Atomicity:** ingestion failures roll back any inserted `tender_chunks` rows and set `tenders.status='failed'` with a reason. No orphan rows.
- **Security:** PDF validated by MIME **and** `%PDF-` magic bytes; rate limited via a Redis sliding window keyed by `company_id`; chunk content is never logged (only metadata).

## ⚠️ Free-tier caveat (read before benchmarking)

The Gemini free tier is **15 RPM / 1,500 RPD**. A 100-page bilingual PDF yields ~150–300 chunks → multiple embedding calls → **cannot meet the REQ-001 90-second NFR on the free tier**. The code is correct on either tier; set a **paid Tier-1 key** (`GOOGLE_API_KEY`, 3000 RPM, $0.15/M tokens) to meet the NFR.

## What's not here yet (out of scope for this pass)

- Next.js upload UI (REQ-001 Slice 3)
- LangGraph Supervisor + analysis agents + HITL gate (Weeks 2–3)
- OCR fallback for scanned PDFs (explicitly out of MVP per PRD §4.2 / REQ-001 Alt Flow)
- WebSocket progress streaming (Week 3)
