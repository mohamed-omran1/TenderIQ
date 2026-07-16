/**
 * GoNoGoBadge (REQ-008 Slice 4).
 *
 * A coloured badge that surfaces the Go/No-Go recommendation produced by
 * the report_assembler node. The recommendation is one of three values
 * ("GO" | "REVIEW" | "DECLINE") and is computed by the backend in Python
 * from the effective_score using the fixed thresholds:
 *
 *   score >= 70  → GO       (green)  — "Recommended to Bid"
 *   score 40-69  → REVIEW   (amber)  — "Review Carefully Before Bidding"
 *   score <  40  → DECLINE  (red)    — "Consider Declining This Tender"
 *
 * The frontend MUST NOT recompute the recommendation from the score —
 * the backend is the source of truth (REQ-008 Non-Functional / Correctness).
 * This component just maps the three string values to coloured surfaces.
 *
 * Two sizes:
 *   - "hero"  — full-width banner used at the top of the full report page.
 *   - "chip"  — compact pill used inline in tables or summary cards.
 *
 * Colour values are exact hex tokens matching the rest of the frontend
 * (RiskRadarTable, FeasibilityScoreCard, HITLGate).
 */

import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";

import type { GoNoGo } from "@/lib/api/report";

interface PaletteEntry {
  bg: string;
  border: string;
  text: string;
  iconBg: string;
  iconText: string;
}

type Variant = "hero" | "chip";

/**
 * Exact hex values mandated by REQ-008 §Data Requirements.
 * "go" / "review" / "decline" map 1:1 to the GoNoGo enum.
 */
const PALETTE: Record<GoNoGo, PaletteEntry> = {
  GO: {
    bg: "#D1FAE5",
    border: "#6EE7B7",
    text: "#065F46",
    iconBg: "#A7F3D0",
    iconText: "#047857",
  },
  REVIEW: {
    bg: "#FEF3C7",
    border: "#FCD34D",
    text: "#92400E",
    iconBg: "#FDE68A",
    iconText: "#B45309",
  },
  DECLINE: {
    bg: "#FEE2E2",
    border: "#FCA5A5",
    text: "#B91C1C",
    iconBg: "#FECACA",
    iconText: "#B91C1C",
  },
};

/** Human-readable copy for the hero variant. */
const HERO_COPY: Record<GoNoGo, { title: string; subtitle: string }> = {
  GO: {
    title: "Recommended to Bid",
    subtitle: "Tender aligns well with company profile, risk profile is acceptable.",
  },
  REVIEW: {
    title: "Review Carefully Before Bidding",
    subtitle:
      "Tender has mixed signals. Analyst review of risks and commitments is required.",
  },
  DECLINE: {
    title: "Consider Declining This Tender",
    subtitle:
      "Tender carries material risk or low fit. Decline recommended unless conditions change.",
  },
};

interface GoNoGoBadgeProps {
  /** The Go/No-Go value from the report. */
  value: GoNoGo;
  /** "hero" (default) for the large top-of-report banner; "chip" for compact use. */
  variant?: Variant;
  /** Optional className to extend the container (e.g. for print styles). */
  className?: string;
}

export default function GoNoGoBadge({
  value,
  variant = "hero",
  className,
}: GoNoGoBadgeProps) {
  if (variant === "chip") {
    return <ChipBadge value={value} className={className} />;
  }
  return <HeroBadge value={value} className={className} />;
}

/* ---------- hero variant ---------- */

function HeroBadge({
  value,
  className,
}: {
  value: GoNoGo;
  className?: string;
}) {
  const p = PALETTE[value];
  const copy = HERO_COPY[value];
  const Icon = iconFor(value);

  return (
    <section
      data-testid="go-no-go-badge"
      data-go-no-go={value}
      className={
        className ??
        "flex flex-col items-center gap-3 rounded-xl border-2 px-6 py-8 text-center sm:flex-row sm:items-center sm:gap-5 sm:text-left"
      }
      style={{ backgroundColor: p.bg, borderColor: p.border, color: p.text }}
      role="status"
      aria-label={`Go/No-Go recommendation: ${copy.title}`}
    >
      <div
        className="flex size-16 shrink-0 items-center justify-center rounded-full"
        style={{ backgroundColor: p.iconBg, color: p.iconText }}
      >
        <Icon className="size-8" aria-hidden="true" />
      </div>
      <div className="flex flex-col gap-1">
        <p
          className="text-xs font-semibold uppercase tracking-wider"
          style={{ color: p.iconText }}
        >
          {value === "GO"
            ? "Go"
            : value === "REVIEW"
              ? "Review"
              : "No-Go"}
        </p>
        <h1 className="text-2xl font-bold leading-tight sm:text-3xl">
          {copy.title}
        </h1>
        <p
          className="max-w-2xl text-sm leading-relaxed opacity-90"
          style={{ color: p.text }}
        >
          {copy.subtitle}
        </p>
      </div>
    </section>
  );
}

/* ---------- chip variant ---------- */

function ChipBadge({
  value,
  className,
}: {
  value: GoNoGo;
  className?: string;
}) {
  const p = PALETTE[value];
  const Icon = iconFor(value);

  return (
    <span
      data-testid="go-no-go-chip"
      data-go-no-go={value}
      className={
        className ??
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
      }
      style={{ backgroundColor: p.bg, color: p.text, border: `1px solid ${p.border}` }}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {value}
    </span>
  );
}

function iconFor(value: GoNoGo) {
  switch (value) {
    case "GO":
      return CheckCircle2;
    case "REVIEW":
      return AlertTriangle;
    case "DECLINE":
      return XCircle;
  }
}
