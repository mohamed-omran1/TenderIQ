"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { EvalResultResponse } from "@/lib/api/eval";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS_STYLES: Record<
  EvalResultResponse["overall_status"],
  { bg: string; text: string; label: string }
> = {
  PASS: { bg: "#D1FAE5", text: "#065F46", label: "PASS" },
  FAIL: { bg: "#FEE2E2", text: "#B91C1C", label: "FAIL" },
  PARTIAL: { bg: "#FEF3C7", text: "#92400E", label: "PARTIAL" },
  NO_DATA: { bg: "#F3F4F6", text: "#374151", label: "NO DATA" },
};

const CATEGORY_LABELS: Record<string, string> = {
  fidic: "FIDIC Clauses",
  penalty: "Penalty Clauses",
  lg_bond: "LG / Bond",
  termination: "Termination",
  other: "Other",
};

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function recallColour(recall: number): string {
  return recall < 0.85 ? "text-amber-600" : "text-emerald-600";
}

function stdDevColour(stdDev: number): string {
  return stdDev > 5.0 ? "text-amber-600" : "text-emerald-600";
}

function CollapsibleToggle({
  open,
  onToggle,
  label,
}: {
  open: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
    >
      {open ? (
        <ChevronDown className="size-4" />
      ) : (
        <ChevronRight className="size-4" />
      )}
      {label}
    </button>
  );
}

export default function EvalResultCard({
  result,
}: {
  result: EvalResultResponse;
}) {
  const [showCategories, setShowCategories] = useState(false);
  const [showDimensions, setShowDimensions] = useState(false);

  const statusStyle = STATUS_STYLES[result.overall_status] ?? STATUS_STYLES.NO_DATA;
  const { risk_radar, scorer, notes, tender_name } = result.result;

  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate">
              Tender: {tender_name || result.tender_id}
            </CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              Run at {formatDate(result.run_at)} · $
              {result.total_cost_usd.toFixed(4)} USD
            </p>
          </div>
          <span
            className="inline-flex h-5 shrink-0 items-center rounded-4xl px-2 text-xs font-medium whitespace-nowrap"
            style={{
              backgroundColor: statusStyle.bg,
              color: statusStyle.text,
            }}
          >
            {statusStyle.label}
          </span>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Risk Radar */}
        {risk_radar && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-sm font-semibold">Risk Radar Accuracy</h3>
              <span
                className="inline-flex h-4 shrink-0 items-center rounded-4xl px-1.5 text-[10px] font-medium"
                style={{
                  backgroundColor:
                    risk_radar.pass_fail === "PASS" ? "#D1FAE5" : "#FEE2E2",
                  color:
                    risk_radar.pass_fail === "PASS" ? "#065F46" : "#B91C1C",
                }}
              >
                {risk_radar.pass_fail}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <span className="text-xs text-muted-foreground">Recall</span>
                <p
                  className={`text-lg font-semibold tabular-nums ${recallColour(risk_radar.recall)}`}
                >
                  {(risk_radar.recall * 100).toFixed(1)}%
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Precision</span>
                <p className="text-lg font-semibold tabular-nums">
                  {(risk_radar.precision * 100).toFixed(1)}%
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">F1</span>
                <p className="text-lg font-semibold tabular-nums">
                  {(risk_radar.f1 * 100).toFixed(1)}%
                </p>
              </div>
            </div>

            <p className="text-xs text-muted-foreground mt-1.5">
              {risk_radar.total_matched} of {risk_radar.total_labelled}{" "}
              labelled clauses found ({risk_radar.total_found} total model
              findings)
            </p>

            <div className="mt-2">
              <CollapsibleToggle
                open={showCategories}
                onToggle={() => setShowCategories((o) => !o)}
                label="Show category breakdown"
              />

              {showCategories && (
                <table className="mt-2 w-full text-xs">
                  <thead>
                    <tr className="border-b text-left">
                      <th className="pb-1 font-medium text-muted-foreground">Category</th>
                      <th className="pb-1 font-medium text-muted-foreground">
                        Recall
                      </th>
                      <th className="pb-1 font-medium text-muted-foreground">
                        Matched / Labelled
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {risk_radar.per_category.map((cm) => (
                      <tr key={cm.category} className="border-b last:border-0">
                        <td className="py-1">
                          {CATEGORY_LABELS[cm.category] ?? cm.category}
                        </td>
                        <td
                          className={`py-1 font-medium tabular-nums ${recallColour(cm.recall)}`}
                        >
                          {(cm.recall * 100).toFixed(1)}%
                        </td>
                        <td className="py-1 tabular-nums text-muted-foreground">
                          {cm.matched} / {cm.labelled}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        )}

        {/* Scorer Consistency */}
        {scorer && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-sm font-semibold">
                Feasibility Scorer Consistency
              </h3>
              <span
                className="inline-flex h-4 shrink-0 items-center rounded-4xl px-1.5 text-[10px] font-medium"
                style={{
                  backgroundColor:
                    scorer.pass_fail === "PASS" ? "#D1FAE5" : "#FEE2E2",
                  color:
                    scorer.pass_fail === "PASS" ? "#065F46" : "#B91C1C",
                }}
              >
                {scorer.pass_fail}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <span className="text-xs text-muted-foreground">Scores</span>
                <p className="text-sm font-semibold tabular-nums">
                  {scorer.scores.join(", ")}
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Mean</span>
                <p className="text-lg font-semibold tabular-nums">
                  {scorer.mean.toFixed(1)}
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Std Dev</span>
                <p
                  className={`text-lg font-semibold tabular-nums ${stdDevColour(scorer.std_dev)}`}
                >
                  {scorer.std_dev.toFixed(2)}
                </p>
              </div>
            </div>

            <p className="text-xs text-muted-foreground mt-1.5">
              Target: std deviation &le; 5.0 points
            </p>

            {Object.keys(scorer.dimension_ranges).length > 0 && (
              <div className="mt-2">
                <CollapsibleToggle
                  open={showDimensions}
                  onToggle={() => setShowDimensions((o) => !o)}
                  label="Show dimension ranges"
                />

                {showDimensions && (
                  <table className="mt-2 w-full text-xs">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="pb-1 font-medium text-muted-foreground">
                          Dimension
                        </th>
                        <th className="pb-1 font-medium text-muted-foreground">
                          Min Score
                        </th>
                        <th className="pb-1 font-medium text-muted-foreground">
                          Max Score
                        </th>
                        <th className="pb-1 font-medium text-muted-foreground">
                          Range
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(scorer.dimension_ranges).map(
                        ([dim, [min, max]]) => {
                          const range = max - min;
                          return (
                            <tr
                              key={dim}
                              className="border-b last:border-0"
                            >
                              <td className="py-1">{dim}</td>
                              <td className="py-1 tabular-nums">
                                {min.toFixed(1)}
                              </td>
                              <td className="py-1 tabular-nums">
                                {max.toFixed(1)}
                              </td>
                              <td
                                className={`py-1 font-medium tabular-nums ${range > 5 ? "text-amber-600" : "text-emerald-600"}`}
                              >
                                {range.toFixed(1)}
                              </td>
                            </tr>
                          );
                        },
                      )}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </section>
        )}

        {/* Notes */}
        {notes && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {notes}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
