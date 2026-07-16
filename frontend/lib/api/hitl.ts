/**
 * Typed client for HITL override gate endpoints (REQ-007 Slice 2).
 *
 * POST /tenders/{id}/approve
 * POST /tenders/{id}/override
 *
 * Mirrors the Pydantic schemas in backend/app/schemas/analysis.py.
 */

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type HITLResponse = {
  run_id: string;
  action: "approved" | "overridden";
  original_score: number;
  overridden_score: number | null;
  message: string;
};

export type HITLOverride = {
  run_id: string;
  action: "approved" | "overridden";
  original_score: number;
  overridden_score: number | null;
  created_at: string;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
  }
}

export class AuthError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? "Authentication failed");
    this.name = "AuthError";
  }
}

export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

export class ValidationError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(
      typeof detail === "string"
        ? detail
        : "Validation failed",
    );
    this.name = "ValidationError";
  }
}

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

async function handleResponse(res: Response): Promise<HITLResponse> {
  if (res.status === 409) {
    const detail = await readDetail(res);
    throw new ConflictError(
      detail || "Run is not awaiting review.",
    );
  }

  if (res.status === 403) {
    throw new AuthError(res.status, await res.text());
  }

  if (res.status === 422) {
    const body = await res.json().catch(() => ({}));
    throw new ValidationError(res.status, body.detail ?? body);
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as HITLResponse;
}

export async function approveRun(
  tenderId: string,
  justification?: string,
): Promise<HITLResponse> {
  const body: Record<string, unknown> = {};
  if (justification !== undefined && justification.trim().length > 0) {
    body.justification = justification;
  }

  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/approve`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
      },
      body: Object.keys(body).length > 0 ? JSON.stringify(body) : "{}",
    },
  );

  return handleResponse(res);
}

export async function overrideRun(
  tenderId: string,
  overriddenScore: number,
  justification: string,
): Promise<HITLResponse> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/override`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        overridden_score: overriddenScore,
        justification,
      }),
    },
  );

  return handleResponse(res);
}

export async function getHITLOverride(
  tenderId: string,
): Promise<HITLOverride | null> {
  const res = await fetch(
    `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/hitl-override`,
    {
      method: "GET",
      headers: authHeader(),
    },
  );

  if (res.status === 404) {
    return null;
  }

  if (res.status === 403) {
    throw new AuthError(res.status, await res.text());
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as HITLOverride;
}
