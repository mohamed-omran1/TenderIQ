/**
 * Typed client for the risk-findings endpoint (REQ-004 Slice 4).
 *
 * CONTRACT RULE (senior-fullstack skill): the Pydantic response model === the
 * TypeScript type the frontend consumes. These types mirror
 * backend/app/schemas/analysis.py (RiskFindingResponse) exactly.
 */

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/**
 * One row of the Risk Radar table.
 * `id` is serialised from a UUID by FastAPI → string on the wire.
 */
export interface RiskFindingResponse {
  id: string;
  category: "fidic" | "penalty" | "lg_bond" | "termination" | "other";
  severity: "critical" | "high" | "medium" | "low";
  clause_text: string;
  explanation: string;
  source_chunk_index: number;
  confidence: number;
}

/** Generic non-auth API failure. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
  }
}

/**
 * 401/403 from the backend — caller should prompt for a new API key
 * rather than retry. Distinct from ApiError so the UI can render a
 * dedicated "check your API key" state.
 */
export class AuthError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? `Auth error ${status}`);
    this.name = "AuthError";
  }
}

function authHeader(): HeadersInit {
  if (!API_KEY) {
    throw new Error("NEXT_PUBLIC_API_KEY is not configured");
  }
  return { Authorization: `Bearer ${API_KEY}` };
}

/**
 * GET /tenders/{id}/findings.
 *
 * - On 404: returns [] — the run is not yet complete (or no findings were
 *   persisted). This is a valid state, not an error, so the caller can render
 *   the empty UI without a try/catch.
 * - On 401/403: throws AuthError.
 * - On any other non-2xx: throws ApiError with the status code.
 */
export async function getFindings(
  tenderId: string,
): Promise<RiskFindingResponse[]> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/findings`,
    {
      method: "GET",
      headers: authHeader(),
    },
  );

  if (res.status === 404) {
    return [];
  }

  if (res.status === 401 || res.status === 403) {
    const detail = await res.text();
    throw new AuthError(res.status, detail);
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as RiskFindingResponse[];
}
