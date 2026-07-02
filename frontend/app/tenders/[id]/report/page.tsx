"use client";

/**
 * /tenders/[id]/report — minimal report page wrapper (REQ-004 Slice 4).
 *
 * Scope per imp-slice-04_req-04.md:
 *   - title + subtitle
 *   - amber info banner
 *   - <RiskRadarTable>
 *   - placeholder sections for Feasibility / Financial (REQ-005/006)
 *   - disabled "Approve & Generate Full Report" button (HITL comes in REQ-007)
 *
 * The full report UI (ReportViewer, HITL gate) lands in REQ-008.
 *
 * Wraps the tree in a local TanStack Query QueryClient so this page can fetch
 * findings independently from the rest of the app — mirrors the pattern in
 * /profile and /tenders/[id].
 */

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Info } from "lucide-react";

import RiskRadarTable from "@/components/RiskRadarTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function ReportPage() {
  const params = useParams<{ id: string }>();
  const tenderId = params.id;

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

        <div className="grid gap-4 md:grid-cols-2">
          <PlaceholderCard title="Feasibility Score" />
          <PlaceholderCard title="Financial Summary" />
        </div>

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
    </QueryClientProvider>
  );
}

function PlaceholderCard({ title }: { title: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex h-24 items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50 text-xs font-medium text-slate-500">
          Coming in full report
        </div>
      </CardContent>
    </Card>
  );
}
