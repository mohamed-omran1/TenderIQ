/**
 * Typed client for the /eval endpoints (REQ-012 Slice 3).
 *
 * Uses X-Admin-Key header (separate from the Bearer-based company API key
 * auth). This is an admin-only path — company API keys return 403.
 */

const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_KEY ?? "";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export interface CategoryMetrics {
  category: string;
  recall: number;
  precision: number;
  labelled: number;
  found: number;
  matched: number;
}

export interface RiskRadarEvalResult {
  recall: number;
  precision: number;
  f1: number;
  total_labelled: number;
  total_found: number;
  total_matched: number;
  per_category: CategoryMetrics[];
  pass_fail: "PASS" | "FAIL";
}

export interface ScorerConsistencyResult {
  scores: number[];
  mean: number;
  std_dev: number;
  pass_fail: "PASS" | "FAIL";
  dimension_ranges: Record<string, [number, number]>;
}

export interface EvalResultResponse {
  id: string;
  tender_id: string;
  overall_status: "PASS" | "FAIL" | "PARTIAL" | "NO_DATA";
  total_cost_usd: number;
  run_at: string;
  result: {
    eval_id: string;
    tender_name: string;
    risk_radar: RiskRadarEvalResult | null;
    scorer: ScorerConsistencyResult | null;
    total_cost_usd: number;
    overall_status: string;
    notes: string | null;
  };
}

export interface RunEvalRequest {
  tender_id: string;
  run_risk_radar: boolean;
  run_scorer_consistency: boolean;
}

export class AdminAuthError extends Error {
  constructor(message?: string) {
    super(message ?? "Invalid admin key");
    this.name = "AdminAuthError";
  }
}

export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
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

function adminHeader(): HeadersInit {
  return { "X-Admin-Key": ADMIN_KEY };
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export async function runEval(
  request: RunEvalRequest,
): Promise<EvalResultResponse> {
  const res = await fetch(`${API_BASE_URL}/eval/run`, {
    method: "POST",
    headers: {
      ...adminHeader(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (res.status === 403) {
    const detail = await readDetail(res);
    throw new AdminAuthError(detail || "Invalid admin key.");
  }

  if (res.status === 409) {
    const detail = await readDetail(res);
    throw new ConflictError(detail || "Tender must be ingested before running eval.");
  }

  if (res.status === 422) {
    const detail = await readDetail(res);
    throw new ValidationError(detail || "Validation failed.");
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as EvalResultResponse;
}

export async function getEvalResults(
  limit: number = 10,
): Promise<EvalResultResponse[]> {
  const res = await fetch(
    `${API_BASE_URL}/eval/results?limit=${encodeURIComponent(limit)}`,
    {
      method: "GET",
      headers: adminHeader(),
    },
  );

  if (res.status === 403) {
    const detail = await readDetail(res);
    throw new AdminAuthError(detail || "Invalid admin key.");
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as EvalResultResponse[];
}
