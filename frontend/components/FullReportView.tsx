"use client";

/**
 * FullReportView (REQ-008 Slice 4).
 *
 * Pure presentational component that renders the Go/No-Go brief returned
 * by GET /tenders/{id}/report. Renders, in order:
 *
 *   1. <GoNoGoBadge>  (hero variant) — large coloured banner
 *   2. Effective score + analyst override note (if applicable)
 *   3. Recommendation           — 1 clear sentence
 *   4. Executive Summary        — 3-5 sentences
 *   5. Risk Summary             — top 5 findings (table)
 *   6. Feasibility Highlights   — 3-5 bullets
 *   7. Financial Highlights     — 3-5 bullets
 *   8. Analyst Note             — shown only when is_analyst_override=true
 *   9. Print / PDF button       — triggers window.print(); the CSS print
 *                                  stylesheet (@media print in globals.css
 *                                  scope) handles the rest.
 *
 * The "PDF download" is intentionally implemented via the browser's
 * native print dialog (Save as PDF). This avoids pulling a PDF
 * generation library, keeps the client zero-deps, and matches what
 * tender analysts already do with Word/PDF reports.
 *
 * If the report contains the `error` field (the fallback report emitted
 * by the report_assembler node on LLM failure — REQ-008 Slice 2
 * Alternative Flow), a prominent amber warning banner is shown at the
 * top with the supplied executive_summary copy.
 *
 * Colours follow the same hex tokens as RiskRadarTable / FeasibilityScoreCard.
 */

import { useCallback } from "react";
import {
  AlertTriangle,
  CalendarClock,
  Download,
  Printer,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

import GoNoGoBadge from "@/components/GoNoGoBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  ReportResponse,
  RiskSummaryItem,
  GoNoGo,
} from "@/lib/api/report";

interface FullReportViewProps {
  report: ReportResponse;
}

/** Severity ordering for the risk table — matches RiskRadarTable. */
const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;
type Severity = (typeof SEVERITY_ORDER)[number];

const SEVERITY_PALETTE: Record<
  Severity,
  { bg: string; text: string; border: string; label: string }
> = {
  critical: { bg: "#FEE2E2", text: "#B91C1C", border: "#FCA5A5", label: "Critical" },
  high: { bg: "#FEF3C7", text: "#92400E", border: "#FCD34D", label: "High" },
  medium: { bg: "#FEF9C3", text: "#713F12", border: "#FDE68A", label: "Medium" },
  low: { bg: "#F3F4F6", text: "#374151", border: "#D1D5DB", label: "Low" },
};

/** Human-readable category labels (matches RiskRadarTable). */
const CATEGORY_LABELS: Record<string, string> = {
  fidic: "FIDIC Clause",
  penalty: "Penalty",
  lg_bond: "LG / Bond",
  termination: "Termination",
  other: "Other",
};

function formatScore(score: number): string {
  return score.toFixed(1);
}

function formatDateTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Detect the "fallback" report shape emitted by the report_assembler when
 * the LLM call fails twice in a row (REQ-008 Alternative Flow). The
 * fallback is identified by the presence of an `error` key — the
 * structural fields may still be populated, but the analyst must be told
 * the synthesis was incomplete.
 */
function isFallbackReport(
  report: ReportResponse,
): report is ReportResponse & { error: string } {
  return typeof (report as { error?: unknown }).error === "string";
}

export default function FullReportView({ report }: FullReportViewProps) {
  const handlePrint = useCallback(() => {
    if (typeof window !== "undefined") {
      window.print();
    }
  }, []);

  const generatedAt = formatDateTime(report.completed_at);
  const fallback = isFallbackReport(report);

  return (
    <div className="space-y-6 print:space-y-4">
      {/* Print / PDF button — hidden in print output via .print:hidden */}
      <div className="flex items-center justify-end print:hidden">
        <Button
          onClick={handlePrint}
          variant="outline"
          size="sm"
          aria-label="Download report as PDF"
        >
          <Printer className="size-4" />
          Download as PDF
        </Button>
      </div>

      {fallback ? <FallbackBanner /> : null}

      <GoNoGoBadge value={report.go_no_go} />

      <ScoreRow
        effectiveScore={report.effective_score}
        isOverride={report.is_analyst_override}
        goNoGo={report.go_no_go}
        generatedAt={generatedAt}
      />

      {report.is_analyst_override && report.analyst_note ? (
        <AnalystNoteCard note={report.analyst_note} />
      ) : null}

      <RecommendationCard recommendation={report.recommendation} />

      <ExecutiveSummaryCard summary={report.executive_summary} />

      <RiskSummaryTable items={report.risk_summary} />

      <HighlightsCard
        title="Feasibility Highlights"
        icon={<Sparkles className="size-4" />}
        items={report.feasibility_highlights}
        emptyMessage="No feasibility highlights were produced."
      />

      <HighlightsCard
        title="Financial Highlights"
        icon={<ShieldAlert className="size-4" />}
        items={report.financial_highlights}
        emptyMessage="No financial highlights were produced."
      />

      <ReportMeta report={report} />
    </div>
  );
}

/* ---------- Fallback banner ---------- */

function FallbackBanner() {
  return (
    <div
      className="flex items-start gap-3 rounded-lg border-2 p-4 text-sm"
      style={{
        backgroundColor: "#FEF3C7",
        borderColor: "#FCD34D",
        color: "#92400E",
      }}
      role="alert"
    >
      <AlertTriangle className="mt-0.5 size-5 shrink-0" />
      <div>
        <p className="font-semibold">Report synthesis incomplete</p>
        <p className="mt-1 leading-relaxed">
          Automated synthesis failed. The findings below are partial — please
          review the agent outputs manually before drawing a conclusion.
        </p>
      </div>
    </div>
  );
}

/* ---------- Score row ---------- */

function ScoreRow({
  effectiveScore,
  isOverride,
  goNoGo,
  generatedAt,
}: {
  effectiveScore: number;
  isOverride: boolean;
  goNoGo: GoNoGo;
  generatedAt: string | null;
}) {
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-4 py-5">
        <div className="flex flex-col gap-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Effective Feasibility Score
          </p>
          <p className="text-4xl font-bold tabular-nums text-slate-900">
            {formatScore(effectiveScore)}
            <span className="ml-1 text-base font-medium text-slate-400">
              / 100
            </span>
          </p>
          <p className="text-xs text-slate-500">
            Recommendation: <span className="font-semibold">{goNoGo}</span>
            {isOverride ? (
              <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-800">
                Analyst Override
              </span>
            ) : null}
          </p>
        </div>
        {generatedAt ? (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <CalendarClock className="size-4" />
            <span>Generated {generatedAt}</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/* ---------- Analyst note ---------- */

function AnalystNoteCard({ note }: { note: string }) {
  return (
    <div
      className="rounded-lg border p-4 text-sm"
      style={{
        backgroundColor: "#EFF6FF",
        borderColor: "#93C5FD",
        color: "#1E3A8A",
      }}
    >
      <p className="text-xs font-semibold uppercase tracking-wider opacity-80">
        Analyst Note
      </p>
      <p className="mt-1 leading-relaxed">{note}</p>
    </div>
  );
}

/* ---------- Recommendation ---------- */

function RecommendationCard({ recommendation }: { recommendation: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Download className="size-4" />
          Recommendation
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-base leading-relaxed text-slate-800">
          {recommendation}
        </p>
      </CardContent>
    </Card>
  );
}

/* ---------- Executive summary ---------- */

function ExecutiveSummaryCard({ summary }: { summary: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Executive Summary</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700">
          {summary}
        </p>
      </CardContent>
    </Card>
  );
}

/* ---------- Risk summary table ---------- */

function RiskSummaryTable({ items }: { items: RiskSummaryItem[] }) {
  if (!items || items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Risks</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-600">
            No risk findings in this report.
          </div>
        </CardContent>
      </Card>
    );
  }

  /**
   * Sort client-side by severity rank (critical first) so the table
   * never depends on backend ordering. The backend already sends a
   * sorted list, but we re-sort defensively — this is a presentational
   * component, and a misordered API response must not produce a
   * misordered table.
   */
  const rank = (s: string): number => {
    const idx = (SEVERITY_ORDER as readonly string[]).indexOf(s);
    return idx === -1 ? SEVERITY_ORDER.length : idx;
  };
  const sorted = [...items].sort(
    (a, b) => rank(a.severity) - rank(b.severity),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Top Risks
          <span className="ml-2 text-sm font-normal text-slate-500">
            ({sorted.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">
                <th className="px-4 py-2">Severity</th>
                <th className="px-4 py-2">Category</th>
                <th className="px-4 py-2">Description</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((item, idx) => (
                <RiskSummaryRow
                  key={`${item.category}-${idx}`}
                  item={item}
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function RiskSummaryRow({ item }: { item: RiskSummaryItem }) {
  const sev = (SEVERITY_ORDER as readonly string[]).includes(item.severity)
    ? (item.severity as Severity)
    : null;
  const palette = sev ? SEVERITY_PALETTE[sev] : null;
  const categoryLabel =
    CATEGORY_LABELS[item.category] ?? item.category;
  const severityLabel = palette?.label ?? item.severity;

  return (
    <tr className="border-b border-slate-100 last:border-b-0 align-top">
      <td className="px-4 py-3">
        {palette ? (
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold"
            style={{ backgroundColor: palette.bg, color: palette.text }}
          >
            {severityLabel}
          </span>
        ) : (
          <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
            {severityLabel}
          </span>
        )}
      </td>
      <td className="px-4 py-3 text-slate-700">{categoryLabel}</td>
      <td className="px-4 py-3 leading-relaxed text-slate-800">
        {item.description}
      </td>
    </tr>
  );
}

/* ---------- Highlights list (feasibility or financial) ---------- */

function HighlightsCard({
  title,
  icon,
  items,
  emptyMessage,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
  emptyMessage: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {icon}
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-slate-500">{emptyMessage}</p>
        ) : (
          <ul className="space-y-2 text-sm leading-relaxed text-slate-700">
            {items.map((line, idx) => (
              <li key={idx} className="flex gap-2">
                <span
                  className="mt-2 size-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: "#94A3B8" }}
                  aria-hidden="true"
                />
                <span className="whitespace-pre-line">{line}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/* ---------- Metadata footer ---------- */

function ReportMeta({ report }: { report: ReportResponse }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-500 print:mt-6">
      <p>
        Report ID: <span className="font-mono">{report.run_id}</span> · Tender
        ID: <span className="font-mono">{report.tender_id}</span>
      </p>
      {report.is_analyst_override ? (
        <p className="mt-1">
          Effective score reflects analyst override applied during HITL review.
        </p>
      ) : null}
    </div>
  );
}

/* ---------- Skeleton (exported for the page to use while loading) ---------- */

export function FullReportSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-40 w-full rounded-xl" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}
