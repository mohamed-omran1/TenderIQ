"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  ArrowRight,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getRunStatus,
  type RunState,
  type RunStatusResponse,
} from "@/lib/api/analysis";

interface AgentStreamViewerProps {
  tenderId: string;
}

const NODE_KEYS = [
  "supervisor",
  "risk_radar",
  "scorer",
  "financial",
  "aggregator",
] as const;

const PARALLEL_NODES = new Set<string>(["risk_radar", "scorer", "financial"]);

const NODE_LABELS: Record<string, string> = {
  supervisor: "Supervisor",
  risk_radar: "Risk Radar",
  scorer: "Feasibility Scorer",
  financial: "Financial Analyst",
  aggregator: "Aggregator",
};

type NodeStatus = "PENDING" | "RUNNING" | "COMPLETE";

function inferNodeStatus(
  nodeKey: string,
  agentTrace: Record<string, unknown>,
  runState: RunState,
): NodeStatus {
  if (nodeKey in agentTrace) return "COMPLETE";

  const isActive = runState === "pending" || runState === "running";
  if (!isActive) return "PENDING";

  if (nodeKey === "supervisor") return "RUNNING";

  if (PARALLEL_NODES.has(nodeKey)) {
    return "supervisor" in agentTrace ? "RUNNING" : "PENDING";
  }

  if (nodeKey === "aggregator") {
    const allParallelDone = [...PARALLEL_NODES].every((n) => n in agentTrace);
    return allParallelDone ? "RUNNING" : "PENDING";
  }

  return "PENDING";
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

const STATE_BANNER: Record<
  RunState,
  { label: string; tone: string }
> = {
  pending: {
    label: "Preparing analysis...",
    tone: "border-slate-200 bg-slate-50 text-slate-700",
  },
  running: {
    label: "Analysis in progress",
    tone: "border-blue-200 bg-blue-50 text-blue-800",
  },
  awaiting_hitl: {
    label: "Ready for your review",
    tone: "border-amber-200 bg-amber-50 text-amber-800",
  },
  complete: {
    label: "Analysis complete",
    tone: "border-emerald-200 bg-emerald-50 text-emerald-800",
  },
  failed: {
    label: "Analysis failed",
    tone: "border-rose-200 bg-rose-50 text-rose-800",
  },
};

export default function AgentStreamViewer({
  tenderId,
}: AgentStreamViewerProps) {
  const [elapsed, setElapsed] = useState(0);

  const { data, isError, error } = useQuery<RunStatusResponse>({
    queryKey: ["run-status", tenderId],
    queryFn: () => getRunStatus(tenderId),
    refetchInterval: (query) =>
      query.state.data?.state === "pending" ||
      query.state.data?.state === "running"
        ? 2000
        : false,
  });

  useEffect(() => {
    if (!data?.started_at) return;

    const isActive = data.state === "pending" || data.state === "running";
    const startTime = new Date(data.started_at).getTime();

    if (!isActive) {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
      return;
    }

    const update = () => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    };

    update();
    const interval = setInterval(update, 1000);

    return () => clearInterval(interval);
  }, [data?.started_at, data?.state]);

  if (isError) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
        <AlertCircle className="size-4" />
        Failed to load analysis status.{" "}
        {error instanceof Error ? error.message : ""}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="size-4 animate-spin" />
        Loading analysis status...
      </div>
    );
  }

  const banner = STATE_BANNER[data.state];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Analysis Status</span>
            {data.started_at && (
              <span className="text-sm font-normal text-slate-500">
                {formatElapsed(elapsed)}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className={`rounded-lg border p-3 text-sm font-medium ${banner.tone}`}
          >
            {banner.label}
          </div>

          {data.state === "failed" && data.error_reason && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              {data.error_reason}
            </div>
          )}

          {data.state === "awaiting_hitl" && (
            <a
              href={`/tenders/${tenderId}/report`}
              className="mt-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-medium text-amber-800 hover:bg-amber-100 transition-colors"
            >
              View report and review findings
              <ArrowRight className="size-4" />
            </a>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pipeline Nodes</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {NODE_KEYS.map((key) => {
              const status = inferNodeStatus(key, data.agent_trace, data.state);
              return (
                <NodeRow key={key} label={NODE_LABELS[key]} status={status} />
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function NodeRow({
  label,
  status,
}: {
  label: string;
  status: NodeStatus;
}) {
  return (
    <div className="flex items-center gap-3">
      {status === "COMPLETE" && (
        <CheckCircle2 className="size-5 shrink-0 text-emerald-500" />
      )}
      {status === "RUNNING" && (
        <span className="relative flex size-5 shrink-0 items-center justify-center">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-40" />
          <Circle className="size-3 fill-blue-500 text-blue-500" />
        </span>
      )}
      {status === "PENDING" && (
        <Circle className="size-5 shrink-0 text-slate-300" />
      )}
      <span
        className={`text-sm ${
          status === "PENDING"
            ? "text-slate-400"
            : "text-slate-800"
        }`}
      >
        {label}
      </span>
    </div>
  );
}
