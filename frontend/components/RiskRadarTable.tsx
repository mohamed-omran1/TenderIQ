"use client";

/**
 * RiskRadarTable (REQ-004 Slice 4).
 *
 * Reads findings via TanStack Query v5, groups by severity in a hardcoded
 * order (critical → high → medium → low), and renders one collapsible row
 * per finding. The expand/collapse state is per-row via useState.
 *
 * Rules enforced here (see imp-slice-04_req-04.md):
 *   - No fetch() in components; all calls go through lib/api/findings.ts.
 *   - 404 from the API is treated as empty (run not yet complete), not error.
 *   - Severity order is hardcoded — never inferred from the API response.
 *   - Category labels use a human-readable mapping; raw enums never reach UI.
 *   - All severity / confidence colours are exact hex values.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, AlertCircle } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import {
  getFindings,
  ApiError,
  AuthError,
  type RiskFindingResponse,
} from "@/lib/api/findings";

interface RiskRadarTableProps {
  tenderId: string;
}

type Severity = RiskFindingResponse["severity"];
type Category = RiskFindingResponse["category"];

/**
 * Hardcoded severity order. The backend already orders within each severity
 * bucket, but the frontend is the one that decides which bucket to render
 * first — never sort by the order findings arrive in.
 */
const SEVERITY_ORDER: readonly Severity[] = [
  "critical",
  "high",
  "medium",
  "low",
] as const;

/**
 * Hex values mandated by imp-slice-04_req-04.md. Using inline Tailwind
 * arbitrary-value classes (bg-[#…]) so the exact colour reaches the DOM
 * without relying on the theme's named tokens.
 */
const SEVERITY_COLORS: Record<
  Severity,
  { bg: string; text: string; border: string; label: string }
> = {
  critical: {
    bg: "#FEE2E2",
    text: "#B91C1C",
    border: "#FCA5A5",
    label: "Critical",
  },
  high: {
    bg: "#FEF3C7",
    text: "#92400E",
    border: "#FCD34D",
    label: "High",
  },
  medium: {
    bg: "#FEF9C3",
    text: "#713F12",
    border: "#FDE68A",
    label: "Medium",
  },
  low: {
    bg: "#F3F4F6",
    text: "#374151",
    border: "#D1D5DB",
    label: "Low",
  },
};

/**
 * Human-readable category labels. Raw enums (lg_bond, fidic, …) must never
 * reach the user — this is the single source of truth for the mapping.
 */
const CATEGORY_LABELS: Record<Category, string> = {
  fidic: "FIDIC Clause",
  penalty: "Penalty",
  lg_bond: "LG / Bond",
  termination: "Termination",
  other: "Other",
};

function confidenceColor(c: number): { bg: string; text: string } {
  // Exact breakpoints from the slice spec: >= 0.8 green, 0.5–0.79 amber,
  // < 0.5 grey.
  if (c >= 0.8) return { bg: "#16A34A", text: "#16A34A" };
  if (c >= 0.5) return { bg: "#D97706", text: "#D97706" };
  return { bg: "#6B7280", text: "#6B7280" };
}

function groupBySeverity(
  findings: RiskFindingResponse[],
): Record<Severity, RiskFindingResponse[]> {
  const groups: Record<Severity, RiskFindingResponse[]> = {
    critical: [],
    high: [],
    medium: [],
    low: [],
  };
  for (const f of findings) {
    groups[f.severity].push(f);
  }
  return groups;
}

export default function RiskRadarTable({ tenderId }: RiskRadarTableProps) {
  const {
    data: findings,
    isLoading,
    isError,
    error,
  } = useQuery<RiskFindingResponse[]>({
    queryKey: ["findings", tenderId],
    queryFn: () => getFindings(tenderId),
  });

  if (isLoading) {
    return <RiskRadarSkeleton />;
  }

  if (isError) {
    // 404 is never thrown from getFindings (it returns []), so any error
    // here is either a real API failure or an AuthError.
    const message =
      error instanceof AuthError
        ? "Authentication failed. Check your API key."
        : error instanceof ApiError
          ? `Failed to load findings (HTTP ${error.status}).`
          : "Failed to load findings.";
    return (
      <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
        <AlertCircle className="mt-0.5 size-4 shrink-0" />
        {message}
      </div>
    );
  }

  if (!findings || findings.length === 0) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-600">
        No risk clauses identified in this tender.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SummaryBar findings={findings} />
      <GroupedFindings findings={findings} />
    </div>
  );
}

function SummaryBar({ findings }: { findings: RiskFindingResponse[] }) {
  const counts: Record<Severity, number> = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
  };
  for (const f of findings) counts[f.severity] += 1;

  const total = findings.length;

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
      <span className="font-medium text-slate-900">
        {total} {total === 1 ? "finding" : "findings"}
      </span>
      <span className="text-slate-300">—</span>
      {SEVERITY_ORDER.map((sev) => {
        const n = counts[sev];
        if (n === 0) return null;
        return (
          <span key={sev} className="flex items-center gap-1.5">
            <span
              className="font-semibold tabular-nums"
              style={{ color: SEVERITY_COLORS[sev].text }}
            >
              {n} {SEVERITY_COLORS[sev].label}
            </span>
            {sev !== "low" && <span className="text-slate-300">•</span>}
          </span>
        );
      })}
    </div>
  );
}

function GroupedFindings({ findings }: { findings: RiskFindingResponse[] }) {
  const groups = groupBySeverity(findings);

  return (
    <div className="space-y-6">
      {SEVERITY_ORDER.map((sev) => {
        const items = groups[sev];
        if (items.length === 0) return null;
        return (
          <SeverityGroup key={sev} severity={sev} findings={items} />
        );
      })}
    </div>
  );
}

function SeverityGroup({
  severity,
  findings,
}: {
  severity: Severity;
  findings: RiskFindingResponse[];
}) {
  const colors = SEVERITY_COLORS[severity];
  return (
    <section>
      <header
        className="rounded-t-md border border-b-0 px-4 py-2 text-xs font-semibold uppercase tracking-wider"
        style={{
          backgroundColor: colors.bg,
          color: colors.text,
          borderColor: colors.border,
        }}
      >
        {colors.label} · {findings.length}
      </header>
      <Card className="rounded-t-none border-t-0">
        <CardContent className="divide-y divide-slate-100 p-0">
          {findings.map((f) => (
            <FindingRow key={f.id} finding={f} />
          ))}
        </CardContent>
      </Card>
    </section>
  );
}

function FindingRow({ finding }: { finding: RiskFindingResponse }) {
  const [expanded, setExpanded] = useState(false);
  const colors = SEVERITY_COLORS[finding.severity];
  const conf = confidenceColor(finding.confidence);

  return (
    <div className="space-y-3 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold"
          style={{ backgroundColor: colors.bg, color: colors.text }}
        >
          {colors.label}
        </span>
        <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
          {CATEGORY_LABELS[finding.category]}
        </span>
        <span className="text-xs text-slate-400">
          Source: chunk #{finding.source_chunk_index}
        </span>
      </div>

      <div>
        <div
          className={
            expanded
              ? "text-sm leading-relaxed text-slate-800 whitespace-pre-wrap"
              : "text-sm leading-relaxed text-slate-800 whitespace-pre-wrap line-clamp-3"
          }
        >
          {finding.clause_text}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-900"
        >
          {expanded ? (
            <>
              <ChevronUp className="size-3" />
              Show less
            </>
          ) : (
            <>
              <ChevronDown className="size-3" />
              Show full clause
            </>
          )}
        </button>
      </div>

      <p className="text-sm text-slate-700">{finding.explanation}</p>

      <div>
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>Model confidence</span>
          <span
            className="font-medium tabular-nums"
            style={{ color: conf.text }}
          >
            {Math.round(finding.confidence * 100)}%
          </span>
        </div>
        <div
          className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(finding.confidence * 100)}
        >
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.max(0, Math.min(100, finding.confidence * 100))}%`,
              backgroundColor: conf.bg,
            }}
          />
        </div>
      </div>
    </div>
  );
}

function RiskRadarSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-8 w-1/4" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}
