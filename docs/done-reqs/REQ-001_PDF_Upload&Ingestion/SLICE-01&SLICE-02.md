All 11 todos complete. **26 tests collected, lint clean, all imports resolve.**

## Summary — REQ-001 PDF Upload & Ingestion (Slices 1 + 2 + 4) implemented

I built the full upload→ingestion→pgvector pipeline per the approved plan. The implementation went through the project's own skills as guardrails: `version-resolver` (mandatory version manifest before code), `database-designer` (TEXT+CHECK, HNSW cosine ops, tenant-scoped FK indexes), `rag-architect` (multilingual chunking, injectable embeddings), `senior-fullstack` (lifespan, Annotated deps, BOLA-scoped queries), `api-security-reviewer` (magic bytes + BOLA + rate limit + sanitization), `ai-security` (T5: never log chunk content), and `senior-qa` (transactional rollback fixtures, stubbed LLM, tenant-isolation class).

### What's built
| Layer | Files |
|---|---|
| **Infra** | `docker-compose.yml` (pgvector/pg16 + redis), `pyproject.toml`, `.env.example`, `README.md` |
| **DB** | `models.py` (4 tables, tenant-scoped), hand-written Alembic `0001` migration with pgvector extension + HNSW `vector_cosine_ops` + CHECK constraints |
| **API** | `POST /tenders/upload` (202 async), `GET /tenders/{id}`, auth (bcrypt Bearer), Redis sliding-window rate limit (429+Retry-After), monthly quota |
| **Ingestor** | PyMuPDF page extraction → script-ratio language detection (ar/en/mixed) → paragraph-aware chunking (500–800 tok, overlap) → Gemini `gemini-embedding-001` @768 (tenacity retry on 429) → pgvector, with **atomic failure cleanup** (no orphan chunks) |
| **Tests** | 26 cases: all 6 Alt Flows + postconditions + orphan cleanup + tenant isolation + auth + quota + sanitization |

### Honest caveats surfaced (all in code/README)
1. **"Gemini 1.5 Flash" can't embed** — Flash is a chat model. I used `gemini-embedding-001` (also free on Gemini's free tier, 100+ languages). Corrected with you up front.
2. **Free tier (15 RPM / 1500 RPD) can't meet the 90s/100-page NFR** — code is correct on any tier; meeting the NFR needs a paid Tier-1 key (`GOOGLE_API_KEY`).
3. **`tender_chunks.company_id` is a deliberate PRD §7 deviation** — denormalized for safe/fast tenant-scoped retrieval; documented in the migration.
4. **Arabic end-to-end test skips gracefully** when no Arabic-capable TTF is present (reportlab's default Helvetica has no Arabic glyphs); the language detector itself is covered by passing unit tests.

### Verified here
- Lint: clean. Imports: clean. **26 tests collected, 7 unit tests passing** (filename sanitization + language detection).
- The 19 DB-backed tests need a live Postgres+pgvector — run via `docker compose up -d && alembic upgrade head && pytest -q` (documented in README). This sandbox has no Docker/Postgres, so I verified correctness via static checks, pure-function unit tests, and inspecting actual PyMuPDF extraction output.

### Deferred (per your scope choice)
Slice 3 (Next.js upload UI), LangGraph graph wiring + analysis agents + HITL gate (Weeks 2–3), OCR (out of MVP), WebSocket streaming (Week 3).