## Slice 4 (QA) — complete ✅

**Result: 25 passed, 1 skipped, 0 failed. Lint clean. Zero real provider calls.**

The prior slices' done-doc claimed "26 tests collected, 7 unit tests passing… the 19 DB-backed tests need a live Postgres." When I actually ran them against the live DB, **all 19 DB-backed tests failed** — the suite had never executed end-to-end. Slice 4's Definition of Done ("automated tests return the documented status code") required them to *pass*, so the real work was making them green.

### Root causes found & fixed (all within Slice 4's owned `tests/` files)
| # | Bug | Fix | File |
|---|---|---|---|
| 1 | asyncpg `Future attached to a different loop` — global `_test_engine` pool reused connections across per-test event loops | `poolclass=NullPool` on the test engine | `conftest.py` |
| 2 | `ModuleNotFoundError: No module named 'tests'` | added `tests/__init__.py` | `tests/__init__.py` |
| 3 | Upload-flow BackgroundTask ran the **real** ingestor (no API key → `EmbeddingUnavailable`) | stubbed the router's `run_ingestion` to a no-op | `test_tenders_upload.py` |
| 4 | `ingestion_session` patched `app.db.session.with_session`, but the ingestors bound their **own** ref → real production connection committed outside the rollback → `UniqueViolation` cross-test pollution | patch the consuming modules (`app.agents.ingestion`, `app.agents.nodes.ingestor`) | `conftest.py` |
| 5 | `stub_embeddings` patched the factory, not the ingestor's bound ref → real **billed Gemini calls** leaked into CI | patch `ingestor.get_embeddings_client` too | `conftest.py` |
| 6 | Ingestion tests ran ingestion **twice** (BackgroundTask + explicit call) → unique-constraint violation | neutralized the router's background task, drive ingestion only via the explicit call | `test_ingestion_pipeline.py` |

### Acceptance Criteria mapping (REQ-001 §DoD)
- ✅ **202 + tender_id < 2s** → `TestUploadHappyPath::test_valid_pdf_returns_202_with_tender_id`
- ✅ **All 6 Alternative Flows return documented codes** → 422 (`TestAlternativeFlowNotPdf`, +polyglot magic-bytes), 413 (`...Oversize`), 429+`Retry-After` (`...RateLimit`), corrupt/scanned → `failed` w/ reason (`...Corrupt`, `...Scanned`), embedding failure → `failed` (`...EmbeddingFailure`)
- ✅ **tender_chunks have non-null embeddings on success** → `test_valid_pdf_ingests_to_ready_with_embeddings`
- ✅ **No orphan chunks after failure (atomic cleanup)** → `TestNoOrphanChunks::test_failed_run_leaves_no_chunks`
- ✅ **Rate limit 429 + Retry-After per tenant** → `...RateLimit` (incl. per-tenant isolation)
- ✅ **detected_language correctness** → 4 language unit tests + Arabic e2e *skips gracefully* where no Arabic TTF is present (detector itself covered)
- ⏭️ Frontend distinct UI states = **Slice 3's** acceptance criterion (not Slice 4)
- ℹ️ 90s/100-page NFR needs a real large fixture + paid tier — outside this slice's single-page fixtures

### Scope discipline
I touched only Slice 4's owned files: `tests/conftest.py`, `tests/test_tenders_upload.py`, `tests/test_ingestion_pipeline.py`, `tests/__init__.py`. No production code changed — every bug was a test-harness/wiring defect. The one notable finding I did **not** silently fix: the Alembic migration `0001` records itself as applied (`alembic_version=0001`) but **never creates the business tables** — tests only pass because conftest's `Base.metadata.create_all` builds them. That's a Slice 1 database-designer concern I'm flagging for you rather than expanding scope.

A note on the `done-reqs` doc: it overstates status ("26 tests collected… 7 passing"). I'd recommend moving/adding a Slice 4 done-doc reflecting the real verified state. Want me to write that `REQ-001-SLICE-04.md` under `docs/done-reqs/`, and/or commit these changes?