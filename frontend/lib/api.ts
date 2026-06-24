/**
 * Typed client for the TenderIQ backend.
 *
 * This is the seam between the FastAPI backend and the Next.js frontend. Two
 * responsibilities:
 *   1. Attach the bearer API key and POST multipart to /tenders/upload.
 *   2. Translate every failure into the `UploadError` discriminated union so
 *      the UI can switch on `kind` and render a distinct state per REQ-001
 *      Alternative Flow (not a generic "something went wrong").
 *
 * The client is intentionally framework-agnostic (plain fetch) so it can be
 * unit-tested without React.
 */
import type {
  TenderDetailResponse,
  TenderUploadResponse,
  UploadError,
} from "./types";

/** Resolve the API base URL once. Overridable per-environment via env var. */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Default poll interval for GET /tenders/{id} (ms). Tunable for tests. */
export const DEFAULT_POLL_INTERVAL_MS = 1500;

/** Give up polling after this long so a stuck "processing" status can't hang forever. */
export const DEFAULT_POLL_TIMEOUT_MS = 120_000;

/**
 * Map a raw fetch response (after upload) to a typed UploadError.
 *
 * Reads the backend's `{"detail": "..."}` error envelope (app/main.py
 * api_error_handler) and the `Retry-After` header on 429.
 */
async function toUploadError(
  res: Response,
): Promise<Extract<UploadError, { kind: string }>> {
  // Try to parse the FastAPI error envelope, but never trust it for control
  // flow — status code is the source of truth.
  let detail = "";
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    detail = typeof body.detail === "string" ? body.detail : "";
  } catch {
    // Non-JSON body (e.g. proxy error) — fall through to status-based mapping.
  }

  switch (res.status) {
    case 401:
      return { kind: "unauthorized" };
    case 413:
      return { kind: "file_too_large" };
    case 422:
      return { kind: "unsupported_file_type" };
    case 429: {
      // Distinguish rate-limit (transient) from quota (monthly cap) via the
      // backend's detail string — both share 429 but need different UI.
      const retryAfter = res.headers.get("Retry-After");
      const retrySeconds = retryAfter ? Number(retryAfter) : NaN;
      if (/quota/i.test(detail)) {
        return { kind: "quota_exceeded" };
      }
      return {
        kind: "rate_limited",
        retryAfter: Number.isFinite(retrySeconds) ? retrySeconds : null,
      };
    }
    default:
      return { kind: "server_error", status: res.status };
  }
}

export interface UploadOptions {
  /** Bearer API key — resolves the company_id server-side. Never sent in the body. */
  apiKey: string;
  /** Abort the in-flight upload (e.g. user clicked cancel). */
  signal?: AbortSignal;
}

/**
 * POST /tenders/upload.
 *
 * @returns the 202 body on success, or a typed UploadError on failure.
 * Never throws on HTTP errors — only on network failure (returned as
 * `{ kind: "network" }`) so callers can use a simple switch.
 */
export async function uploadTender(
  file: File,
  opts: UploadOptions,
): Promise<{ ok: true; data: TenderUploadResponse } | { ok: false; error: UploadError }> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/tenders/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${opts.apiKey}` },
      body: form,
      signal: opts.signal,
    });
  } catch {
    // fetch throws only on network-level failure (DNS, CORS, offline, abort).
    // A user-initiated abort is not an error to surface as "upload failed".
    if (opts.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    return { ok: false, error: { kind: "network" } };
  }

  if (!res.ok) {
    return { ok: false, error: await toUploadError(res) };
  }

  const data = (await res.json()) as TenderUploadResponse;
  return { ok: true, data };
}

export interface GetTenderOptions {
  apiKey: string;
  signal?: AbortSignal;
}

/** GET /tenders/{id} — tenant-scoped status lookup used by the poller. */
export async function getTender(
  tenderId: string,
  opts: GetTenderOptions,
): Promise<TenderDetailResponse | null> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${opts.apiKey}` },
      signal: opts.signal,
    });
  } catch {
    if (opts.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    // Transient network blip during polling — return null so the poller retries
    // rather than terminating the whole upload flow.
    return null;
  }

  if (!res.ok) return null;
  return (await res.json()) as TenderDetailResponse;
}
