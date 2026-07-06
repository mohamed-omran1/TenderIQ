"use client";

/**
 * /tenders/[id]/report — REQ-004 Slice 4 baseline, extended in REQ-005
 * Slice 4, extended in REQ-006 Slice 4.
 *
 * Sections, top to bottom:
 *   1. Header (title + tender id).
 *   2. Amber info banner — preliminary review.
 *   3. <RiskRadarTable>  (REQ-004)
 *   4. <FeasibilityScoreCard>  (REQ-005)  — backed by useQuery(getAggregatedResults)
 *   5. <FinancialSummaryCard>  (REQ-006)  — owns its own useQuery(getFinancialCommitments)
 *   6. Disabled "Approve & Generate Full Report" button  (REQ-007 still pending)
 *
 * TanStack Query v5 syntax is used verbatim: the spec mandates the
 * queryKey / queryFn / enabled triple, which is what the analyst-facing
 * "live refetch on revisit" UX depends on. The QueryClient is created
 * lazily via useState to keep the page self-contained — same pattern as
 * /tenders/[id] and /profile.
 */

import { useState } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { AlertCircle, Info } from "lucide-react";

import RiskRadarTable from "@/components/RiskRadarTable";
import FeasibilityScoreCard from "@/components/FeasibilityScoreCard";
import FinancialSummaryCard from "@/components/FinancialSummaryCard";
import { Button } from "@/components/ui/button";
import {
  getAggregatedResults,
  ApiError,
  AuthError,
} from "@/lib/api/analysis";

export default function ReportPage() {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 0,
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ReportPageBody />
    </QueryClientProvider>
  );
}

function ReportPageBody() {
  const params = useParams<{ id: string }>();
  const tenderId = params.id;

  const {
    data,
    isError,
    error,
  } = useQuery({
    queryKey: ["aggregated-results", tenderId],
    queryFn: () => getAggregatedResults(tenderId),
    enabled: !!tenderId,
  });

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          Risk Analysis Report
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Tender ID: {tenderId}
        </p>
      </header>

      <div
        className="flex items-start gap-2 rounded-lg border p-3 text-sm"
        style={{
          backgroundColor: "#FEF3C7",
          borderColor: "#FCD34D",
          color: "#92400E",
        }}
      >
        <Info className="mt-0.5 size-4 shrink-0" />
        <p>
          This is a preliminary risk review. The full Go/No-Go report will be
          available after your review and approval below.
        </p>
      </div>

      <RiskRadarTable tenderId={tenderId} />

      {isError ? <QueryErrorBanner error={error} /> : null}

      <FeasibilityScoreCard
        tenderId={tenderId}
        score={data?.feasibility_score ?? null}
        breakdown={data?.feasibility_breakdown ?? null}
      />

      <FinancialSummaryCard tenderId={tenderId} />

      <div className="flex flex-col items-stretch gap-2 pt-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-500">
          HITL approval — coming in REQ-007
        </p>
        <Button
          disabled
          aria-disabled
          title="HITL approval — coming in REQ-007"
          className="sm:ml-auto"
        >
          Approve &amp; Generate Full Report
        </Button>
      </div>
    </main>
  );
}

function QueryErrorBanner({ error }: { error: unknown }) {
  const message =
    error instanceof AuthError
      ? "Authentication failed. Check your API key."
      : error instanceof ApiError
        ? `Failed to load aggregated results (HTTP ${error.status}).`
        : "Failed to load aggregated results.";
  return (
    <div
      className="flex items-start gap-2 rounded-lg border p-3 text-sm"
      style={{
        backgroundColor: "#FEE2E2",
        borderColor: "#FCA5A5",
        color: "#B91C1C",
      }}
      role="alert"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <p>{message}</p>
    </div>
  );
}
