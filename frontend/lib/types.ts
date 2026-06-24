/**
 * Types mirroring the TenderIQ backend schemas (app/schemas/tender.py) and the
 * typed API errors (app/errors.py).
 *
 * CONTRACT RULE (senior-fullstack skill): the Pydantic response model === the
 * TypeScript type the frontend consumes. If these drift, that's the bug.
 * Keep this file in sync with backend/app/schemas/tender.py.
 */

/** Tender lifecycle status — matches the `tenders.status` enum. */
export type TenderStatus = "uploading" | "processing" | "ready" | "failed";

/** Dominant language across chunks. `null` until ingestion resolves it. */
export type PrimaryLanguage = "ar" | "en" | "bilingual";

/** POST /tenders/upload — 202 Accepted body (TenderUploadResponse). */
export interface TenderUploadResponse {
  /** Stable UUID; references all downstream endpoints. */
  tender_id: string;
  status: TenderStatus;
}

/** GET /tenders/{id} — 200 body (TenderDetailResponse). Tenant-scoped. */
export interface TenderDetailResponse {
  id: string;
  filename: string;
  status: TenderStatus;
  primary_language: PrimaryLanguage | null;
  page_count: number | null;
  file_size_bytes: number;
  error_reason: string | null;
  uploaded_at: string; // ISO-8601 datetime serialized as string over JSON
}

/**
 * Discriminated union for every error the upload flow can produce.
 *
 * One entry per Alternative Flow in REQ-001, plus the cross-cutting transport /
 * auth failures. The component switches on `kind` to render a distinct UI state
 * for each — matching the REQ-001 acceptance criterion "distinct UI states for
 * each failure type".
 */
export type UploadError =
  | { kind: "unsupported_file_type" } // 422 — not a PDF
  | { kind: "file_too_large" } // 413 — over 50MB
  | { kind: "rate_limited"; retryAfter: number | null } // 429 — Retry-After header (seconds)
  | { kind: "quota_exceeded" } // 429 — monthly doc limit
  | { kind: "unauthorized" } // 401 — missing/invalid API key
  | { kind: "not_found" } // 404 — tender vanished / wrong tenant
  | { kind: "server_error"; status: number } // any other non-2xx
  | { kind: "network" } // fetch threw (CORS, DNS, offline)
  | { kind: "ingestion_failed"; reason: string | null }; // status=failed from polling
