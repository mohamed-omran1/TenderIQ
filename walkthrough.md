Slice 4 (QA) — Test Suite Verification Walkthrough
Environment Summary
Component	Version / Status
Python (venv)	3.12.7
Docker	29.5.3
PostgreSQL + pgvector	pgvector/pgvector:pg16 — healthy
Redis	redis:7-alpine — healthy
Alembic migrations	At head
pip .[dev] install	All deps satisfied
Test Run Results

25 passed, 1 skipped in 59.47s
NOTE

The 1 skip is test_arabic_pdf_detected_as_ar — correctly gated by _arabic_extraction_works() because this Windows machine lacks an Arabic TTF font that reportlab can embed into the test PDF fixture. Arabic language detection itself is covered by 4 passing unit tests (see below).

REQ-001 Acceptance Criteria ↔ Test Coverage Mapping
✅ AC1: POST /tenders/upload accepts a valid PDF and returns 202 with a tender_id within 2 seconds
Test	File	Result
test_valid_pdf_returns_202_with_tender_id	
test_tenders_upload.py
✅ PASSED
test_upload_creates_tender_row_with_uploading_status	
test_tenders_upload.py
✅ PASSED
✅ AC3: tender_chunks rows are created with non-null embedding vectors for 100% of extracted chunks
Test	File	Result
test_valid_pdf_ingests_to_ready_with_embeddings	
test_ingestion_pipeline.py
✅ PASSED
Assertions verified: status == "ready", primary_language == "en", page_count == 1, every chunk has embedding is not None with len(embedding) == 768, and detected_language in {"ar", "en", "mixed"}.

✅ AC4: detected_language is correctly assigned for at least 90% of chunks
Test	File	Result
test_english	
test_ingestion_pipeline.py
✅ PASSED
test_arabic	
test_ingestion_pipeline.py
✅ PASSED
test_mixed	
test_ingestion_pipeline.py
✅ PASSED
test_primary_aggregation	
test_ingestion_pipeline.py
✅ PASSED
test_arabic_pdf_detected_as_ar	
test_ingestion_pipeline.py
⏭️ SKIPPED (no Arabic font on this machine)
✅ AC5: All 6 Alternative Flow scenarios are covered by automated tests and return the documented status code
Alt Flow	REQ-001 Spec	Test	Status Code	Result
1. Not a PDF	422	test_non_pdf_mime_returns_422	422	✅ PASSED
1b. Polyglot	422	test_pdf_mime_but_no_magic_bytes_returns_422	422	✅ PASSED
1c. No DB row	—	test_no_db_row_created_for_non_pdf	—	✅ PASSED
2. Oversized	413	test_oversized_file_returns_413	413	✅ PASSED
3. Rate limit	429 + Retry-After	test_rate_limit_returns_429_with_retry_after	429	✅ PASSED
3b. Per-tenant	—	test_rate_limit_is_per_tenant	—	✅ PASSED
4. Corrupt PDF	status='failed'	test_corrupt_pdf_fails_with_reason	—	✅ PASSED
5. Scanned PDF	status='failed', scan/OCR reason	test_scanned_pdf_fails_with_scanned_reason	—	✅ PASSED
6. Embedding failure	status='failed' after retries	test_embedding_failure_marks_failed	—	✅ PASSED
✅ AC6: No orphaned tender_chunks rows exist after a failed ingestion run
Test	File	Result
test_failed_run_leaves_no_chunks	
test_ingestion_pipeline.py
✅ PASSED
✅ AC7: Rate limiting returns 429 with a Retry-After header once a company exceeds its tier quota
Test	File	Result
test_rate_limit_returns_429_with_retry_after	
test_tenders_upload.py
✅ PASSED
test_quota_exceeded_blocks_upload	
test_tenders_upload.py
✅ PASSED
Additional Security Coverage (OWASP API Top 10)
Test	Scenario	Result
test_tenant_cannot_read_other_tenants_tender	BOLA/IDOR — tenant B cannot read tenant A's tender (returns 404)	✅ PASSED
test_owner_can_read_own_tender	Positive — owner can read their own tender	✅ PASSED
test_missing_bearer_returns_401	Missing auth header → 401	✅ PASSED
test_invalid_bearer_returns_401	Invalid API key → 401	✅ PASSED
test_strips_path_components	Path traversal defence in filename	✅ PASSED
test_strips_control_chars	Log injection defence in filename	✅ PASSED
test_reject_oversize_declared	Pre-buffer size rejection	✅ PASSED
AC Items Not Covered by Slice 4 (Per Scope)
AC	Owner	Notes
AC2: 100-page bilingual PDF completes in <90s	Slice 2 + perf benchmark	Needs a 100-page fixture and a real (or stubbed) Gemini key with timing assertions
AC8: Frontend upload component shows distinct UI states	Slice 3 (Frontend)	Out of scope for backend QA
Summary
No code changes were needed. The existing test suite runs green against the real Postgres+pgvector database with the following characteristics:

All 14 test_tenders_upload.py tests pass — covering upload happy path, all 3 HTTP-level alt flows (422, 413, 429), auth, tenant isolation, monthly quota, and input sanitization
All 10 test_ingestion_pipeline.py tests pass (1 skipped for Arabic font) — covering ingestion happy path, corrupt PDF, scanned PDF, embedding failure, orphan cleanup, and language detection
Tests use transactional rollback (no cross-test pollution), stub embeddings (no real Gemini calls), and fakeredis (deterministic rate-limit tests)
The conftest's event_loop + NullPool + savepoint pattern works correctly with pytest-asyncio