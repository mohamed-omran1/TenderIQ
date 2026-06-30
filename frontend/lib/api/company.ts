/**
 * Typed client for the company-profile endpoints (REQ-002 Slice 3).
 *
 * CONTRACT RULE (senior-fullstack skill): the Pydantic response model === the
 * TypeScript type the frontend consumes. These types mirror
 * backend/app/schemas/company.py exactly.
 */

// TODO: move to shared api client in REQ-011
const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

/** Base URL of the TenderIQ FastAPI backend — never hardcoded to localhost. */
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/** Nested financial-capacity object. Mirrors app/schemas/company.py. */
export interface FinancialCapacity {
  currency: string;
  annual_turnover: number;
  available_bonding_capacity: number;
}

/** One reference project. Mirrors app/schemas/company.py. */
export interface PastProject {
  name: string;
  value: number;
  year: number;
  sector: string;
}

/**
 * Full profile shape returned by GET/PUT and accepted by PUT.
 * company_id / updated_at are response-only — the backend sets them.
 */
export interface CompanyProfileSchema {
  specializations: string[];
  financial_capacity: FinancialCapacity;
  geographic_reach: string[];
  past_projects: PastProject[];
  max_project_value: number;
  company_id?: string | null;
  updated_at?: string | null;
}

/**
 * Empty-profile shape returned by GET when no profile exists yet.
 * All fields are nullable/empty so the frontend can render a blank form
 * without defensive null-checks on the root object (REQ-002 Usability NFR).
 */
export interface EmptyProfileResponse {
  specializations: string[] | null;
  financial_capacity: FinancialCapacity | null;
  geographic_reach: string[] | null;
  past_projects: PastProject[] | null;
  max_project_value: number | null;
  company_id: string | null;
  updated_at: string | null;
}

export type CompanyProfileApiResponse =
  | CompanyProfileSchema
  | EmptyProfileResponse;

/** Generic non-validation API failure. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
  }
}

/** Field-level detail emitted by FastAPI on HTTP 422. */
export interface FieldError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** Structured validation failure with field-level detail. */
export class ValidationError extends Error {
  constructor(
    public readonly status: number,
    public readonly details: FieldError[],
  ) {
    super("Validation failed");
    this.name = "ValidationError";
  }
}

function authHeader(): HeadersInit {
  if (!API_KEY) {
    // Surface a clear runtime error instead of sending an invalid header.
    throw new Error("NEXT_PUBLIC_API_KEY is not configured");
  }
  return { Authorization: `Bearer ${API_KEY}` };
}

/**
 * GET /company-profile.
 *
 * Always returns 200 — either a full profile or the empty-profile shape.
 */
export async function getCompanyProfile(): Promise<CompanyProfileApiResponse> {
  const res = await fetch(`${API_BASE_URL}/company-profile`, {
    method: "GET",
    headers: authHeader(),
  });

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as CompanyProfileApiResponse;
}

/**
 * PUT /company-profile.
 *
 * Upserts the profile. On 422 throws a ValidationError with field-level detail;
 * on any other error throws an ApiError with the status code.
 */
export async function updateCompanyProfile(
  data: CompanyProfileSchema,
): Promise<CompanyProfileSchema> {
  const res = await fetch(`${API_BASE_URL}/company-profile`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
    },
    body: JSON.stringify(data),
  });

  if (res.status === 422) {
    const body = (await res.json()) as { detail?: FieldError[] };
    throw new ValidationError(res.status, body.detail ?? []);
  }

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return (await res.json()) as CompanyProfileSchema;
}
