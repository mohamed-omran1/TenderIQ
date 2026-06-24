# TenderIQ — Project Skills

Domain-specific ZCode skills for building TenderIQ. Each skill lives in its own directory under `.agents/skills/<name>/SKILL.md` and triggers on relevant work; ZCode auto-discovers them.

## Stack context baked into every skill

- **Backend:** FastAPI (async) · LangGraph multi-agent pipeline · LangChain · PostgreSQL + `pgvector` · Redis
- **Frontend:** Next.js (App Router) / TypeScript
- **Domain:** B2B SaaS — automated tender-document analysis for construction/procurement (Egypt & GCC)
- **Source-of-truth docs:** `docs/01_PRD.md`, `docs/02_Architecture.md`

## Stable versions referenced (verified June 2026 via Context7)

| Library | Version |
| --- | --- |
| Next.js | v16.x stable |
| FastAPI | 0.128.x |
| Pydantic | v2 |
| LangGraph | current (`StateGraph`, `interrupt_before`, `PostgresSaver`/`AsyncPostgresSaver`) |
| pgvector | current (HNSW) |

Re-confirm with Context7 before any upgrade — these move. Use `version-resolver` (below) to do this systematically.

## Skills

### Meta-skill (runs first, before any code/skill generation)

| Skill | When it triggers | Owns |
| --- | --- | --- |
| [`version-resolver`](./version-resolver/SKILL.md) | Any task that installs packages, scaffolds a project, writes imports, or needs a known library version | Forces a Context7 version-fetch + version manifest **before** code generation. Training data is outdated; never skip when versions are relevant |

### Domain skills

| Skill | When it triggers | Owns |
| --- | --- | --- |
| [`senior-fullstack`](./senior-fullstack/SKILL.md) | Cross-layer features, scaffolding, request lifecycle, frontend↔backend seam | End-to-end implementation, contract parity, tenant boundary across layers |
| [`database-designer`](./database-designer/SKILL.md) | Tables, migrations, indexes, vector schema, tenant isolation | PostgreSQL + pgvector schema, Alembic, HNSW, audit tables |
| [`senior-qa`](./senior-qa/SKILL.md) | Tests, eval harness, flakiness, CI gates | pytest + Vitest + Playwright + the `/eval/run` recall benchmark |
| [`agent-designer`](./agent-designer/SKILL.md) | LangGraph nodes, state schema, parallel fan-out, HITL gate | Graph topology, `TenderState`, reducers, resume-after-interrupt |
| [`rag-architect`](./rag-architect/SKILL.md) | Chunking, embeddings, vector search, multilingual, OCR | Ingestor pipeline, embedding model choice, retrieval recall |
| [`senior-prompt-engineer`](./senior-prompt-engineer/SKILL.md) | Writing/revising agent prompts, recall/cost tradeoffs | Risk Radar / Scorer / Financial / Assembler prompts, structured output, eval-gated iteration |
| [`ai-security`](./ai-security/SKILL.md) | LLM threat modeling — injection, leakage, tenant contamination | T1–T7 threat model for the untrusted-PDF → agent path |
| [`api-security-reviewer`](./api-security-reviewer/SKILL.md) | HTTP/WS endpoint review, auth, rate limit, OWASP API Top 10 | FastAPI auth/BOLA/rate-limit/upload-safety review |

## How they fit together

```
                ┌─────────────────────────────────────────────────────┐
                │  [version-resolver]  ← runs FIRST: resolve versions  │
                │       via Context7 before any code is written        │
                └────────────────────────┬────────────────────────────┘
                                         │ version manifest
                                         ▼
Upload PDF ──► [rag-architect] ingest + chunk + embed
                    │
                    ▼
            [database-designer] tender_chunks (HNSW)
                    │
                    ▼
   [agent-designer] LangGraph: Supervisor ─► (risk_radar | scorer | financial)
                    │                              │
                    │  prompts ◄── [senior-prompt-engineer]
                    │                              │
                    ▼                              ▼
               aggregator ──► HITL gate ──► report_assembler
                    │
                    ▼
   [senior-fullstack]  WS stream + REST  ──►  Next.js frontend
                    │
                    ▼
   [senior-qa] unit / graph / eval / e2e   +   [ai-security] + [api-security-reviewer]
```

## Editing these skills

Follow `skill-creator` guidance: descriptions are the trigger signal (keep them specific and a little pushy), bodies stay under ~500 lines, and real examples beat more rules. Update the version table above whenever `version-resolver` (or a Context7 check) shows a newer stable release you've adopted.
