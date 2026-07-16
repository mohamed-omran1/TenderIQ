/**
 * Typed client for the analysis-run endpoints (REQ-003, REQ-005).
 *
 * This file owns all calls to `/tenders/{id}/analyse`, `/tenders/{id}/status`
 * and the derived `getAggregatedResults` helper used by the report page.
 * The shapes here mirror the Pydantic response models in
 * backend/app/schemas/analysis.py and the aggregator's output dict in
 * backend/app/agents/nodes/aggregator.py.
 */
import { AuthError, type RiskFindingResponse } from "@/lib/api/findings";

/**
 * Re-exported so callers of `getAggregatedResults` (the report page) can
 * import the auth error type from this module alone — keeps the import
 * surface in page components narrow.
 */
export { AuthError };

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type RunState =
  | "pending"
  | "running"
  | "awaiting_hitl"
  | "complete"
  | "failed";

/**
 * Raw wire shape of GET /tenders/{id}/status.
 *
 * `aggregated_results` is the dict the Aggregator node writes into state at
 * the end of the run. The /status endpoint is expected to surface it; if the
 * backend has not yet been updated to include it, `getAggregatedResults` still
 * works — it just returns a `feasibility_breakdown: null` and the
 * FeasibilityScoreCard renders the skeleton for the dimension table.
 */
export type RunStatusResponse = {
  run_id: string;
  state: RunState;
  started_at: string;
  completed_at: string | null;
  error_reason: string | null;
  feasibility_score: number | null;
  agent_trace: Record<string, unknown>;
  aggregated_results?: AggregatedResults | null;
};

export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

export class NotFoundError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotFoundError";
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
  }
}

export type DimensionScore = {
  /** 0–20 (per REQ-005 scoring rubric). */
  score: number;
  rationale: string;
};

export type FeasibilityBreakdown = {
  technical_fit: DimensionScore;
  financial_capacity: DimensionScore;
  timeline: DimensionScore;
  geographic_scope: DimensionScore;
  past_experience: DimensionScore;
};

/**
 * `feasibility_breakdown` is normally a `FeasibilityBreakdown`, but the
 * feasibility_scorer node degrades to `{"error": "..."}` when the LLM
 * response cannot be parsed (REQ-005 Alternative Flow). The union models
 * that fallback so the frontend can render an amber banner instead of a
 * partial table.
 */
export type AggregatedResults = {
  feasibility_score: number | null;
  feasibility_breakdown: FeasibilityBreakdown | { error: string } | null;
  risk_findings: RiskFindingResponse[];
  /** Reserved for REQ-006 (Financial Analyst). */
  financial_summary: unknown;
  source_languages: string[];
};

function authHeader(): HeadersInit {
  if (!API_KEY) {
    throw new Error("NEXT_PUBLIC_API_KEY is not configured");
  }
  return { Authorization: `Bearer ${API_KEY}` };
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export async function triggerAnalysis(
  tenderId: string,
): Promise<{ run_id: string; status: string }> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/analyse`,
    {
      method: "POST",
      headers: authHeader(),
    },
  );

  if (res.status === 409) {
    const detail = await readDetail(res);
    throw new ConflictError(
      detail || "An analysis run is already in progress for this tender.",
    );
  }

  if (res.status === 404) {
    const detail = await readDetail(res);
    throw new NotFoundError(detail || "Tender not found.");
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as { run_id: string; status: string };
}

export async function getRunStatus(
  tenderId: string,
): Promise<RunStatusResponse> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/status`,
    {
      method: "GET",
      headers: authHeader(),
    },
  );

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as RunStatusResponse;
}

/**
 * GET /tenders/{tenderId}/status — then pull `aggregated_results` out.
 *
 * The /status endpoint returns the run's high-level summary
 * (run_id, state, feasibility_score, agent_trace) and, in the success path,
 * the Aggregator's `aggregated_results` dict (risk_findings, feasibility
 * score + breakdown, financial_summary, source_languages). The frontend
 * never needs to call /findings and /status separately to render the report
 * page — this helper centralises the merge.
 *
 * - 404: returns `null`. The run has not started (or has been purged).
 *   Treat as "no data yet" — the page renders a skeleton, not an error.
 * - 401/403: throws `AuthError`. Caller should prompt for a new API key.
 * - any other non-2xx: throws `ApiError`.
 *
 * The extraction is defensive: if the backend's /status response does not
 * include `aggregated_results` (older API surface), the function still
 * returns a well-typed result with `feasibility_breakdown: null` and empty
 * `risk_findings` so the UI degrades gracefully instead of crashing.
 */
export async function getAggregatedResults(
  tenderId: string,
): Promise<AggregatedResults | null> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/status`,
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

  const json = (await res.json()) as Partial<RunStatusResponse>;
  const aggregated = json.aggregated_results ?? null;

  return {
    feasibility_score: json.feasibility_score ?? null,
    feasibility_breakdown: aggregated?.feasibility_breakdown ?? null,
    risk_findings: aggregated?.risk_findings ?? [],
    financial_summary: aggregated?.financial_summary ?? null,
    source_languages: aggregated?.source_languages ?? [],
  };
}
