# TenderIQ — Functional Requirements Document

## REQ-001: PDF Upload & Ingestion

| Attribute | Details |
| :--- | :--- |
| **Status** | **READY FOR IMPLEMENTATION**[cite: 4] |
| **Sprint** | Week 1 — Foundation[cite: 4] |
| **Priority** | P0 — Blocking (all downstream agent nodes depend on this)[cite: 4] |
| **Related Docs** | TenderIQ_PRD_v1.0 §6.2, §7  \|  TenderIQ_Architecture_v1.0 §2, §5[cite: 4] |

### Owning Component
* **FastAPI Router:** `app/api/routers/tenders.py`[cite: 4]
* **Ingestor Node (LangGraph):** `app/agents/nodes/ingestor.py`[cite: 4]
* **Database Tables:** `tenders` / `tender_chunks` (`app/db/models.py`)[cite: 4]

---

### Description
Enable an authenticated company to upload a tender PDF document[cite: 4]. The system stores the file, extracts and chunks its text content while detecting language per chunk (Arabic, English, or mixed), generates embeddings, and persists everything to `pgvector` — making the document ready for downstream agent analysis (Risk Radar, Feasibility Scorer, Financial Analyst)[cite: 4]. This is the entry point of the entire TenderIQ pipeline; no other agent node can run until ingestion completes successfully[cite: 4].

---

### Preconditions
* The requesting company has a valid, active API key (`Authorization: Bearer` header)[cite: 4].
* The company has not exceeded its monthly document upload quota (`companies.monthly_doc_limit`)[cite: 4].
* The uploaded file is a PDF (`MIME type application/pdf`) and does not exceed 50 MB[cite: 4].
* PostgreSQL with the `pgvector` extension is reachable and the `tenders` / `tender_chunks` tables exist (post-migration)[cite: 4].

---

### Main Flow
1. Client sends `POST /tenders/upload` as `multipart/form-data` with the PDF file[cite: 4].
2. FastAPI auth dependency resolves the API key to a `company_id` and checks the rate limit (Redis sliding window)[cite: 4].
3. Backend validates file type and size[cite: 4]. On failure, returns `422` with a specific error message (see Alternative Flows)[cite: 4].
4. Backend stores the raw file to disk (local volume for MVP) and inserts a `tenders` row with `status = "uploading"`[cite: 4].
5. Backend returns `HTTP 202 Accepted` with `{ tender_id, status: "uploading" }` immediately — the client does not wait for ingestion to complete[cite: 4].
6. A background task invokes the Ingestor node, which extracts text page by page using a PDF text-extraction library[cite: 4].
7. For each page, the Ingestor node runs language detection and splits content into chunks (target: 500–800 tokens per chunk, paragraph-boundary aware)[cite: 4].
8. Each chunk is embedded using the configured multilingual embedding model and inserted into `tender_chunks` with its `detected_language`, `chunk_index`, and `embedding` vector[cite: 4].
9. Once all chunks are persisted, `tenders.status` is updated to `"ready"` and `tenders.primary_language` is set based on the dominant language across chunks (`"ar"`, `"en"`, or `"bilingual"` if neither exceeds 70%)[cite: 4].
10. Client polls `GET /tenders/{id}` (or receives a WebSocket event, per Architecture §2 step 5) to detect the `"ready"` status before triggering analysis[cite: 4].

---

### Alternative Flows

| Condition | System Response | Resulting State |
| :--- | :--- | :--- |
| **File is not a PDF** | HTTP 422 — "Only PDF files are supported." | No DB row created.[cite: 4] |
| **File exceeds 50 MB** | HTTP 413 — "File exceeds 50MB limit." | No DB row created.[cite: 4] |
| **Rate limit exceeded** | HTTP 429 with `Retry-After` header. | No DB row created.[cite: 4] |
| **PDF is password-protected / corrupt** | Ingestor node catches extraction error. | `tenders.status = "failed"`, error reason stored.[cite: 4] |
| **PDF is scanned (no extractable text)** | Ingestor detects near-zero extracted text and flags for OCR fallback. | `tenders.status = "failed"` (OCR explicitly out of MVP scope per PRD §4.2).[cite: 4] |
| **Embedding API call fails mid-run** | Retry with exponential backoff (3 attempts). | On exhausted retries: `tenders.status = "failed"`.[cite: 4] |

---

### Postconditions
* **On success:** `tenders.status = "ready"`, `tender_chunks` contains all chunks with non-null embedding vectors, and `tenders.primary_language` is populated[cite: 4].
* **On any failure path:** `tenders.status = "failed"` with a human-readable error reason persisted, and no partial/orphaned `tender_chunks` rows remain (cleanup on failure is atomic)[cite: 4].
* The `tender_id` is stable and can be referenced by all subsequent endpoints (`/analyse`, `/report`, etc.) regardless of success or failure[cite: 4].

---

### Data Requirements

#### Table: `tenders`
* **Fields Written:** `id`, `company_id`, `filename`, `storage_path`, `status`, `primary_language`, `uploaded_at`[cite: 4]
* **Notes:** status enum: `uploading` | `processing` | `ready` | `failed`[cite: 4]

#### Table: `tender_chunks`
* **Fields Written:** `id`, `tender_id`, `chunk_index`, `content`, `detected_language`, `embedding`[cite: 4]
* **Notes:** `embedding` is a `pgvector` column; HNSW index applied via migration[cite: 4]

---

### Non-Functional Requirements

* **Performance**
  * A 100-page bilingual PDF must complete full ingestion (extraction + chunking + embedding) in under 90 seconds[cite: 4].
  * The upload endpoint itself (steps 1–5 of Main Flow) must respond in under 2 seconds, independent of document size — ingestion happens asynchronously[cite: 4].
* **Security**
  * Uploaded files are scoped strictly to `company_id`; no endpoint may return another tenant's tender data[cite: 4].
  * Stored PDFs are encrypted at rest (provider-managed encryption, per Architecture §6.3)[cite: 4].
  * File content is never logged in plaintext; only metadata (filename, size, page count) appears in application logs[cite: 4].
* **Reliability**
  * Ingestion failures must never leave the `tenders` row in an indefinite `"uploading"` or `"processing"` state — every path terminates in `"ready"` or `"failed"`[cite: 4].
  * Embedding generation calls must use retry-with-backoff (see Alternative Flows) to tolerate transient API failures[cite: 4].
* **Usability**
  * Error messages returned to the client must be specific enough to act on (e.g. distinguish "file too large" from "unsupported format"), never a generic 500[cite: 4].

---

### Implementation Slices
Each slice is implemented and reviewed independently, in the order listed. An agent working on a given slice should not modify files outside its stated scope.*[cite: 4]

1. **Backend**
   * **Owns:** `routers/tenders.py`, `db/models.py`, alembic migration[cite: 4]
   * **Scope:** Implement `POST /tenders/upload`, the `tenders` + `tender_chunks` tables, and validation/rate-limit logic[cite: 4]. No Ingestor logic yet — stub it to set `status="ready"` with zero chunks so the endpoint is independently testable[cite: 4].
2. **Agent Node**
   * **Owns:** `agents/nodes/ingestor.py`, `agents/state.py`[cite: 4]
   * **Scope:** Implement real PDF extraction, language detection, chunking, and embedding generation[cite: 4]. Wire into the background task triggered by Slice 1's endpoint[cite: 4].
3. **Frontend**
   * **Owns:** `app/upload/page.tsx`, `components/TenderUpload.tsx`[cite: 4]
   * **Scope:** Drag-and-drop upload UI calling `POST /tenders/upload`, with polling or WebSocket status updates and clear error states matching Alternative Flows[cite: 4].
4. **QA**
   * **Owns:** `tests/test_tenders_upload.py`[cite: 4]
   * **Scope:** Test cases derived directly from Alternative Flows + Postconditions: valid PDF, oversized file, wrong MIME type, rate-limit exceeded, corrupt PDF, scanned PDF[cite: 4].

---

### Slice Activation Rule
The project owner selects which slice is executed and when — this decision is never delegated to the AI agent. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope (e.g. Slice 1 → senior-fullstack + database-designer). The agent must not expand scope to cover other slices, and must not select the next slice on its own.

### Acceptance Criteria / Definition of Done
* [ ] `POST /tenders/upload` accepts a valid PDF and returns 202 with a `tender_id` within 2 seconds[cite: 4].
* [ ] A bilingual (Arabic + English) 100-page sample PDF completes ingestion in under 90 seconds, end to end[cite: 4].
* [ ] `tender_chunks` rows are created with non-null embedding vectors for 100% of extracted chunks on a successful run[cite: 4].
* [ ] `detected_language` is correctly assigned for at least 90% of chunks when validated against a manually labelled sample (this is the eval threshold for non-deterministic language detection)[cite: 4].
* [ ] All 6 Alternative Flow scenarios are covered by automated tests and return the documented status code[cite: 4].
* [ ] No orphaned `tender_chunks` rows exist after a failed ingestion run (verified via a cleanup test)[cite: 4].
* [ ] Rate limiting returns 429 with a `Retry-After` header once a company exceeds its tier quota[cite: 4].
* [ ] Frontend upload component shows distinct UI states for: uploading, processing, ready, and each failure type[cite: 4].

---

### Document Control
This REQ is the contract for implementation. Any deviation discovered during build (e.g. a new edge case) should be added back into Alternative Flows before the slice is marked complete — not silently handled in code[cite: 4].

