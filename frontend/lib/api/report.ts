/**
 * Typed client for the final-report endpoint (REQ-008 Slice 3 & Slice 4).
 *
 * GET /tenders/{id}/report — the Go/No-Go brief produced by the
 * report_assembler node, written into analysis_runs.agent_trace and
 * surfaced through the API as a typed JSON document.
 *
 * CONTRACT RULE (senior-fullstack skill): the Pydantic response model
 * === the TypeScript type the frontend consumes. These types mirror
 * backend/app/schemas/analysis.py (ReportResponse, RiskSummaryItemResponse)
 * exactly. Any drift between the two surfaces is a bug.
 *
 * Status-code contract (mirrors the router in
 * backend/app/routers/tenders.py):
 *   200 — report available, body is a ReportResponse
 *   404 — run not yet complete (polling signal), or no run for this tender
 *   403 — tenant scope violation (the run belongs to a different company)
 *   401 — invalid/missing API key
 */
const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/**
 * Go/No-Go recommendation. The backend computes this in Python from the
 * effective_score (hitl_override_score if set, else feasibility_score)
 * using the fixed thresholds in REQ-008 §Data Requirements. The frontend
 * MUST NOT recompute this — it is the source of truth from the server.
 */
export type GoNoGo = "GO" | "REVIEW" | "DECLINE";

/**
 * One item in the report's top-5 risk summary.
 * `severity` is the same union as RiskFindingResponse.severity.
 * `category` is a free-form string (matches the risk_findings table
 * category column) so the report can carry it through without us
 * coupling this client to the REQ-004 enum order.
 */
export interface RiskSummaryItem {
  category: string;
  severity: "critical" | "high" | "medium" | "low" | string;
  description: string;
}

/**
 * Wire shape of GET /tenders/{id}/report.
 *
 * `analyst_note` is the override-acknowledgement string set by the
 * Report Assembler when the analyst adjusted the AI score (REQ-007
 * override flow). May be null when no override happened.
 *
 * `completed_at` is the run's completion timestamp — set by
 * `_resume_graph()` (REQ-007) after the report_assembler node finishes.
 * May be null if the run was force-marked complete without a timestamp
 * (shouldn't happen in practice; defensive default).
 */
export interface ReportResponse {
  run_id: string;
  tender_id: string;
  go_no_go: GoNoGo;
  effective_score: number;
  is_analyst_override: boolean;
  executive_summary: string;
  recommendation: string;
  risk_summary: RiskSummaryItem[];
  feasibility_highlights: string[];
  financial_highlights: string[];
  analyst_note: string | null;
  completed_at: string | null;
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
 * lib/api/findings.ts, lib/api/financial.ts, and lib/api/hitl.ts.
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
 * GET /tenders/{id}/report.
 *
 * - On 404: returns null — the run is not yet complete, or no run exists
 *   for this tender. This is a valid "not ready" signal, not an error;
 *   the report page polls this endpoint and treats null as "keep waiting".
 * - On 403: throws AuthError (the run belongs to another company — the
 *   UI should re-prompt for credentials rather than retry).
 * - On 401: throws AuthError.
 * - On any other non-2xx: throws ApiError with the status code.
 */
export async function getReport(
  tenderId: string,
): Promise<ReportResponse | null> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/report`,
    {
      method: "GET",
      headers: authHeader(),
    },
  );

  if (res.status === 404) {
    return null;
  }

  if (res.status === 401 || res.status === 403) {
    throw new AuthError(res.status, await res.text());
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as ReportResponse;
}
