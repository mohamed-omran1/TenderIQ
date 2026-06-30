const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type RunState =
  | "pending"
  | "running"
  | "awaiting_hitl"
  | "complete"
  | "failed";

export type RunStatusResponse = {
  run_id: string;
  state: RunState;
  started_at: string;
  completed_at: string | null;
  error_reason: string | null;
  agent_trace: Record<string, unknown>;
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
