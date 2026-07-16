/**
 * Typed client for the financial-commitments endpoint (REQ-006 Slice 4).
 *
 * CONTRACT RULE (senior-fullstack skill): the Pydantic response model === the
 * TypeScript type the frontend consumes. These types mirror
 * backend/app/schemas/analysis.py (FinancialCommitmentResponse) exactly.
 *
 * commitment_type is a union literal (not a generic string) so the UI can
 * never accidentally branch on a typo. The full set of types matches the
 * values the financial_analyst node writes to the financial_commitments
 * table (REQ-006 §Data Requirements).
 */

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/**
 * One row of the financial commitments table.
 * `id` is serialised from a UUID by FastAPI → string on the wire.
 *
 * Field notes:
 *  - amount_currency: ISO 4217 code (e.g. "SAR", "AED", "USD", "EGP") or
 *    "UNKNOWN" when the source clause had no currency marker. The UI must
 *    never render "UNKNOWN" raw — it is replaced with an amber review badge.
 *  - percentage: % of contract value, used by retention and (optionally) by
 *    liquidated damages cap or by payment milestones expressed as a %.
 *  - source_chunk_index: traceability link to the chunk the value was
 *    extracted from. Nullable when the analyst could not pin the value
 *    to a single chunk (e.g. merged schedule).
 */
export interface FinancialCommitment {
  id: string;
  commitment_type:
    | "bond"
    | "liquidated_damages"
    | "payment_milestone"
    | "retention"
    | "advance_payment"
    | "contract_value";
  amount_value: number | null;
  amount_currency: string | null;
  percentage: number | null;
  description: string;
  needs_review: boolean;
  source_chunk_index: number | null;
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
 * dedicated "check your API key" state. Mirrors the convention used by
 * `lib/api/findings.ts` (REQ-004) and `lib/api/analysis.ts` (REQ-005).
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
 * GET /tenders/{id}/financial.
 *
 * - On 404: returns [] — the run is not yet complete (or no financial
 *   commitments have been persisted). This is a valid state, not an error,
 *   so the caller can render the empty UI without a try/catch.
 * - On 401/403: throws AuthError.
 * - On any other non-2xx: throws ApiError with the status code.
 *
 * The response is expected to be ordered by commitment_type ASC by the
 * backend (per the slice spec). The UI does not rely on that ordering —
 * it groups by type and renders sections in a hardcoded A→F order.
 */
export async function getFinancialCommitments(
  tenderId: string,
): Promise<FinancialCommitment[]> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/financial`,
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

  return (await res.json()) as FinancialCommitment[];
}
