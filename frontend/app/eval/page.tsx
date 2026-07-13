"use client";

import { useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import {
  runEval,
  getEvalResults,
  AdminAuthError,
  type RunEvalRequest,
  type EvalResultResponse,
} from "@/lib/api/eval";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import EvalResultCard from "@/components/EvalResultCard";

function EvalPageContent() {
  const queryClient = useQueryClient();
  const [tenderId, setTenderId] = useState("");
  const [runRiskRadar, setRunRiskRadar] = useState(true);
  const [runScorerConsistency, setRunScorerConsistency] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resultsQuery = useQuery({
    queryKey: ["eval-results"],
    queryFn: () => getEvalResults(10),
  });

  const runMutation = useMutation({
    mutationFn: (req: RunEvalRequest) => runEval(req),
    onSuccess: (newResult) => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["eval-results"] });
      queryClient.setQueryData<EvalResultResponse[]>(
        ["eval-results"],
        (old) => {
          if (!old) return [newResult];
          return [newResult, ...old].slice(0, 10);
        },
      );
    },
    onError: (err: Error) => {
      if (err instanceof AdminAuthError) {
        setError(
          "Admin key not configured. Set NEXT_PUBLIC_ADMIN_KEY in .env.local",
        );
      } else {
        setError(err.message);
      }
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!tenderId.trim()) {
      setError("Tender ID is required.");
      return;
    }

    runMutation.mutate({
      tender_id: tenderId.trim(),
      run_risk_radar: runRiskRadar,
      run_scorer_consistency: runScorerConsistency,
    });
  };

  const handleTenderIdChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTenderId(e.target.value);
    if (error) setError(null);
  };

  return (
    <div className="space-y-6">
      {/* Warning banner */}
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        Admin-only page. Requires NEXT_PUBLIC_ADMIN_KEY to be set in
        environment variables.
      </div>

      {/* Run Eval form */}
      <Card size="sm">
        <CardHeader>
          <CardTitle>Run Evaluation</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="tender-id">Tender ID</Label>
              <Input
                id="tender-id"
                placeholder="UUID of the tender"
                value={tenderId}
                onChange={handleTenderIdChange}
                disabled={runMutation.isPending}
              />
            </div>

            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={runRiskRadar}
                  onChange={(e) => setRunRiskRadar(e.target.checked)}
                  disabled={runMutation.isPending}
                  className="size-4 rounded border-input accent-primary"
                />
                Run Risk Radar Accuracy Eval
              </label>

              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={runScorerConsistency}
                  onChange={(e) => setRunScorerConsistency(e.target.checked)}
                  disabled={runMutation.isPending}
                  className="size-4 rounded border-input accent-primary"
                />
                Run Scorer Consistency Eval
              </label>
            </div>

            <Button
              type="submit"
              disabled={runMutation.isPending || !tenderId.trim()}
              className="w-full"
            >
              {runMutation.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Running evaluation&hellip; this may take up to 3 minutes.
                </>
              ) : (
                "Run Evaluation"
              )}
            </Button>

            {/* Error banner */}
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}
          </form>
        </CardContent>
      </Card>

      {/* Recent Results */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          Recent Evaluations (last 10)
        </h2>

        {resultsQuery.isLoading && (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-40 w-full rounded-xl" />
            ))}
          </div>
        )}

        {resultsQuery.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {resultsQuery.error instanceof AdminAuthError
              ? "Admin key not configured. Set NEXT_PUBLIC_ADMIN_KEY in .env.local"
              : resultsQuery.error.message}
          </div>
        )}

        {resultsQuery.isSuccess && resultsQuery.data.length === 0 && (
          <Card size="sm">
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              No evaluations run yet.
            </CardContent>
          </Card>
        )}

        {resultsQuery.isSuccess && resultsQuery.data.length > 0 && (
          <div className="space-y-3">
            {resultsQuery.data.map((evalResult) => (
              <EvalResultCard key={evalResult.id} result={evalResult} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default function EvalPage() {
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
      <main className="mx-auto max-w-3xl px-4 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">
            Evaluation Dashboard
          </h1>
          <p className="text-muted-foreground">
            Run and review automated accuracy evaluations for the Risk Radar and
            Feasibility Scorer.
          </p>
        </div>
        <EvalPageContent />
      </main>
    </QueryClientProvider>
  );
}
