"use client";

/**
 * FeasibilityScoreCard (REQ-005 Slice 4).
 *
 * Renders the composite feasibility score (0-100), the per-dimension
 * breakdown table (5 rows), and the HITL notice. The card is a
 * pure presentational component — the parent page owns the data fetch
 * via useQuery(getAggregatedResults) and passes the props down.
 *
 * Colour rules (imp-slice-04_req-05.md):
 *   0-39   red    bg #FEE2E2 / text #B91C1C
 *   40-69  amber  bg #FEF3C7 / text #92400E
 *   70-100 green  bg #D1FAE5 / text #065F46
 *
 * The composite and EACH dimension bar use these same thresholds, but
 * dimensions are normalised to a percentage first: (score / 20) * 100.
 * So a dimension scoring 8/20 (40%) is amber, even if the composite is
 * green — dimension colour is per-dimension, never composite-driven.
 *
 * Render order (per spec):
 *   1. If breakdown has the "error" key → amber error banner, hide rest.
 *   2. Else if score is null              → skeleton for Sections A & B,
 *                                            but keep Section C visible.
 *   3. Else                               → full composite + table + HITL.
 */

import { AlertTriangle, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type {
  DimensionScore,
  FeasibilityBreakdown,
} from "@/lib/api/analysis";

interface FeasibilityScoreCardProps {
  tenderId: string;
  score: number | null;
  breakdown: FeasibilityBreakdown | { error: string } | null;
}

type ScoreBand = "red" | "amber" | "green";

interface PaletteEntry {
  bg: string;
  text: string;
  label: string;
}

/**
 * Exact hex values mandated by the slice spec. The values for "red" and
 * "amber" deliberately match REQ-004's RiskRadarTable palette so the
 * report page has a single visual language for severity/tier.
 */
const BAND_PALETTE: Record<ScoreBand, PaletteEntry> = {
  red: { bg: "#FEE2E2", text: "#B91C1C", label: "red" },
  amber: { bg: "#FEF3C7", text: "#92400E", label: "amber" },
  green: { bg: "#D1FAE5", text: "#065F46", label: "green" },
};

/**
 * Apply the 0-39 / 40-69 / 70-100 thresholds to a percentage in [0, 100].
 * Used by both the composite score (percentage = score) and the
 * per-dimension bars (percentage = score/20 * 100).
 */
function bandFor(percentage: number): ScoreBand {
  if (percentage <= 39) return "red";
  if (percentage <= 69) return "amber";
  return "green";
}

function goNoGoLabel(score: number): string {
  if (score <= 39) return "High Risk — Consider Declining";
  if (score <= 69) return "Moderate Fit — Review Carefully";
  return "Strong Fit — Recommended to Bid";
}

/**
 * Human-readable dimension labels. The raw `technical_fit` / etc. keys
 * must never reach the user — this is the single source of truth for the
 * mapping. Order here also defines the display order in the table.
 */
const DIMENSIONS: ReadonlyArray<{
  key: keyof FeasibilityBreakdown;
  label: string;
}> = [
  { key: "technical_fit", label: "Technical Fit" },
  { key: "financial_capacity", label: "Financial Capacity" },
  { key: "timeline", label: "Timeline" },
  { key: "geographic_scope", label: "Geographic Scope" },
  { key: "past_experience", label: "Past Experience" },
];

/** Type guard: backend-emitted "error" breakdown (REQ-005 Alt Flow). */
function isErrorBreakdown(
  breakdown: FeasibilityScoreCardProps["breakdown"],
): breakdown is { error: string } {
  return (
    breakdown !== null &&
    typeof breakdown === "object" &&
    "error" in breakdown &&
    typeof (breakdown as { error: unknown }).error === "string"
  );
}

export default function FeasibilityScoreCard({
  tenderId: _tenderId,
  score,
  breakdown,
}: FeasibilityScoreCardProps) {
  // 1. Malformed-LLM error breakdown takes precedence over everything else.
  if (isErrorBreakdown(breakdown)) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Feasibility Score</CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{
              backgroundColor: "#FEF3C7",
              borderColor: "#FCD34D",
              color: "#92400E",
            }}
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-semibold">
                Scoring encountered an issue. Manual review required.
              </p>
              <p className="mt-1 text-xs opacity-90">{breakdown.error}</p>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  const hasScore = typeof score === "number";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Feasibility Score</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <SectionA score={hasScore ? score : null} />
        <SectionB breakdown={hasScore ? breakdown : null} />
        <SectionC />
      </CardContent>
    </Card>
  );
}

/* ---------- Section A: Composite Score Display ---------- */

function SectionA({ score }: { score: number | null }) {
  if (score === null) {
    return (
      <div className="flex flex-col items-center gap-3 py-2">
        <Skeleton className="size-32 rounded-full" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-40" />
      </div>
    );
  }

  const band = bandFor(score);
  const palette = BAND_PALETTE[band];

  return (
    <div className="flex flex-col items-center gap-2 py-2">
      <div
        className="flex size-32 items-center justify-center rounded-full ring-8 ring-white"
        style={{ backgroundColor: palette.bg }}
        role="img"
        aria-label={`Composite feasibility score: ${Math.round(score)} out of 100`}
      >
        <span
          className="text-4xl font-bold tabular-nums"
          style={{ color: palette.text }}
        >
          {Math.round(score)}
        </span>
      </div>
      <p className="text-sm text-slate-500">out of 100</p>
      <p
        className="text-sm font-semibold"
        style={{ color: palette.text }}
      >
        {goNoGoLabel(score)}
      </p>
    </div>
  );
}

/* ---------- Section B: Dimension Breakdown Table ---------- */

function SectionB({ breakdown }: { breakdown: FeasibilityBreakdown | null }) {
  if (breakdown === null) {
    return (
      <div className="space-y-4" aria-label="Loading dimension breakdown">
        {DIMENSIONS.map((d) => (
          <div key={d.key} className="space-y-2">
            <div className="flex items-center justify-between">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-12" />
            </div>
            <Skeleton className="h-2 w-full rounded-full" />
            <Skeleton className="h-3 w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4" aria-label="Dimension breakdown">
      {DIMENSIONS.map(({ key, label }) => {
        const dim: DimensionScore | undefined = breakdown[key];
        if (!dim) return null;
        return <DimensionRow key={key} label={label} dim={dim} />;
      })}
    </div>
  );
}

function DimensionRow({
  label,
  dim,
}: {
  label: string;
  dim: DimensionScore;
}) {
  // Clamp the raw value into [0, 20] before computing the percentage so
  // an out-of-range LLM emission can never produce a width > 100% or
  // negative. The clamp mirrors the Python-side clamp in
  // feasibility_scorer.py (REQ-005 Non-Functional / Determinism).
  const safeScore = Math.max(0, Math.min(20, dim.score));
  const percentage = (safeScore / 20) * 100;
  const palette = BAND_PALETTE[bandFor(percentage)];

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-slate-800">{label}</span>
        <span className="text-xs font-semibold tabular-nums text-slate-700">
          {safeScore} / 20
        </span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-label={`${label} score`}
        aria-valuemin={0}
        aria-valuemax={20}
        aria-valuenow={safeScore}
      >
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{
            width: `${percentage}%`,
            backgroundColor: palette.bg,
          }}
        />
      </div>
      <p className="text-xs leading-relaxed text-slate-600 whitespace-pre-wrap">
        {dim.rationale}
      </p>
    </div>
  );
}

/* ---------- Section C: HITL Notice ---------- */

function SectionC() {
  return (
    <div
      className="flex flex-col gap-3 rounded-lg border p-3"
      style={{
        backgroundColor: "#FEF3C7",
        borderColor: "#FCD34D",
        color: "#92400E",
      }}
    >
      <div className="flex items-start gap-2 text-sm">
        <Info className="mt-0.5 size-4 shrink-0" />
        <p>
          This score is pending your review. You can adjust it before the
          final report is generated.
        </p>
      </div>
      <div className="flex flex-col items-stretch gap-1 sm:flex-row sm:items-center sm:justify-between">
        <Button
          disabled
          aria-disabled
          title="HITL approval — coming in REQ-007"
          variant="default"
          className="sm:ml-auto"
        >
          Approve &amp; Adjust Score
        </Button>
      </div>
      <p className="text-xs text-slate-500">
        HITL approval — coming in REQ-007
      </p>
    </div>
  );
}
