"use client";

/**
 * /tenders/[id]/report/full — REQ-008 Slice 4.
 *
 * The final report page. Replaces the "coming soon" placeholder of the
 * earlier slice. Renders the Go/No-Go brief produced by the
 * report_assembler node, fetched via GET /tenders/{id}/report.
 *
 * Lifecycle:
 *   - On mount: query getReport(tenderId).
 *       - 404 (null): run is not yet complete → render a polling
 *         skeleton with refetchInterval, plus a "Back to overview" link.
 *       - AuthError (401/403): render a hard error banner.
 *       - ApiError: render a transient error banner with a retry button.
 *       - Success: render <FullReportView>.
 *
 * Polling strategy: 4 seconds while not yet ready. TanStack Query v5
 * handles the interval; the page itself is a Client Component because
 * it owns the query and the polling state (the print dialog also
 * requires the browser, which Server Components can't drive).
 *
 * The back link goes to /tenders/[id]/report (the preliminary review
 * page from REQ-004/005/006) — same pattern as the HITL gate's
 * "Back to overview" navigation.
 */

import { useState, useEffect } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, ArrowLeft, Loader2 } from "lucide-react";

import FullReportView, { FullReportSkeleton } from "@/components/FullReportView";
import { useRunStream } from "@/hooks/useRunStream";
import {
  getReport,
  ApiError,
  AuthError,
  type ReportResponse,
} from "@/lib/api/report";
import { Button } from "@/components/ui/button";

export default function FullReportPage() {
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
      <FullReportPageBody />
    </QueryClientProvider>
  );
}

function FullReportPageBody() {
  const params = useParams<{ id: string }>();
  const tenderId = params.id;

  const { latestEvent, connectionState } = useRunStream(tenderId);

  const {
    data: report,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ReportResponse | null>({
    queryKey: ["report", tenderId],
    queryFn: () => getReport(tenderId),
    enabled: !!tenderId,
    refetchInterval: (query) => {
      if (connectionState !== "error") return false;
      const data = query.state.data;
      if (data === undefined) return 4_000;
      if (data === null) return 4_000;
      return false;
    },
  });

  useEffect(() => {
    if (latestEvent?.event_type === "complete") {
      void refetch();
    }
  }, [latestEvent, refetch]);

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 space-y-6">
      <Header tenderId={tenderId} />

      {isLoading ? (
        <FullReportSkeleton />
      ) : isError ? (
        <ErrorBanner error={error} onRetry={() => void refetch()} />
      ) : report === null || report === undefined ? (
        <NotReadyState />
      ) : (
        <FullReportView report={report} refetch={() => void refetch()} />
      )}
    </main>
  );
}

function Header({ tenderId }: { tenderId: string }) {
  return (
    <header className="flex flex-col gap-2 print:hidden">
      <Link
        href={`/tenders/${encodeURIComponent(tenderId)}/report`}
        className="inline-flex items-center gap-1 text-sm font-medium text-slate-600 hover:text-slate-900"
      >
        <ArrowLeft className="size-4" />
        Back to preliminary review
      </Link>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Final Go/No-Go Report
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Tender ID: {tenderId}
        </p>
      </div>
    </header>
  );
}

function NotReadyState() {
  return (
    <div
      className="flex flex-col items-center gap-4 rounded-xl border border-slate-200 bg-white p-10 text-center"
      role="status"
      aria-live="polite"
    >
      <Loader2 className="size-8 animate-spin text-slate-400" />
      <div>
        <p className="text-base font-semibold text-slate-900">
          Report not ready yet
        </p>
        <p className="mt-1 max-w-md text-sm text-slate-600">
          The Go/No-Go report is being assembled. This page will refresh
          automatically — usually within 30 to 60 seconds.
        </p>
      </div>
    </div>
  );
}

function ErrorBanner({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const message =
    error instanceof AuthError
      ? "Authentication failed. Check your API key."
      : error instanceof ApiError
        ? `Failed to load report (HTTP ${error.status}).`
        : "Failed to load report.";

  return (
    <div
      className="flex flex-col gap-3 rounded-lg border p-4 text-sm"
      style={{
        backgroundColor: "#FEE2E2",
        borderColor: "#FCA5A5",
        color: "#B91C1C",
      }}
      role="alert"
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 size-5 shrink-0" />
        <p>{message}</p>
      </div>
      <div>
        <Button
          onClick={onRetry}
          variant="outline"
          size="sm"
          className="border-rose-300 bg-white text-rose-700 hover:bg-rose-50"
        >
          Retry
        </Button>
      </div>
    </div>
  );
}
