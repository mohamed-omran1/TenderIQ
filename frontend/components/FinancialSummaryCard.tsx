"use client";

/**
 * FinancialSummaryCard (REQ-006 Slice 4).
 *
 * Renders the six financial-commitment sections in a fixed A→F order.
 * The card owns its own data fetch via TanStack Query v5 — same pattern as
 * RiskRadarTable. The parent page (tenders/[id]/report/page.tsx) only
 * provides `tenderId`; the API client lives in `lib/api/financial.ts`.
 *
 * Render contract (imp-slice-04_req-06.md):
 *   - Skeleton while loading.
 *   - Empty array → info banner "No financial commitments identified…"
 *     (404 from the API also reaches this branch via getFinancialCommitments
 *     returning []; not an error).
 *   - AuthError / ApiError → red banner with a specific message.
 *   - 6 sections in hardcoded order A→F, regardless of API response order.
 *   - Section A (Contract Value) always renders, even if the contract value
 *     row is absent — it falls back to muted "Contract value not stated".
 *   - Sections B–F only render if at least one commitment of that type
 *     exists. Sections with no data are completely hidden, never empty.
 *   - "UNKNOWN" currency is never shown raw. The amber "⚠ Requires review"
 *     badge replaces it in every section.
 *   - Monetary values use toLocaleString thousand separators — never raw
 *     floats like 35000000.
 *   - If any commitment has needs_review=true, an amber summary banner is
 *     shown at the bottom of the card.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle, Info } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getFinancialCommitments,
  ApiError,
  AuthError,
  type FinancialCommitment,
} from "@/lib/api/financial";

interface FinancialSummaryCardProps {
  tenderId: string;
}

type CommitmentType = FinancialCommitment["commitment_type"];

/**
 * Hardcoded section order. Sections appear in the order A → F regardless
 * of the order the API returns the commitments. The API sorts by
 * commitment_type ASC; we ignore that and group by type ourselves so the
 * UI contract is stable.
 */
const SECTION_ORDER: ReadonlyArray<{
  key: CommitmentType;
  title: string;
}> = [
  { key: "contract_value", title: "A" },
  { key: "bond", title: "B" },
  { key: "liquidated_damages", title: "C" },
  { key: "payment_milestone", title: "D" },
  { key: "retention", title: "E" },
  { key: "advance_payment", title: "F" },
];

/** Amber palette for "requires review" UI (matches REQ-004/005). */
const AMBER = {
  bg: "#FEF3C7",
  border: "#FCD34D",
  text: "#92400E",
} as const;

/* ---------- formatting helpers ---------- */

/**
 * Format a monetary amount with thousand separators. The currency code
 * is never shown raw if it is missing or "UNKNOWN" — callers branch on the
 * returned `currencyDisplay` to choose between a normal "{CCY} {value}"
 * label and the amber "⚠ Requires review" badge.
 */
function formatAmount(
  value: number | null,
  currency: string | null,
  needsReview: boolean,
): { text: string; showReviewBadge: boolean } {
  if (value === null) {
    return { text: "Amount not specified", showReviewBadge: needsReview };
  }
  const isUnknown = !currency || currency === "UNKNOWN";
  const formatted = value.toLocaleString("en-US", {
    maximumFractionDigits: 0,
  });
  if (isUnknown || needsReview) {
    return { text: formatted, showReviewBadge: true };
  }
  return { text: `${currency} ${formatted}`, showReviewBadge: false };
}

/**
 * Display a "⚠ Currency requires review" / "⚠ Requires review" badge.
 * The exact label varies per section, but the styling is the same amber
 * pill. Matches the inline palette used by RiskRadarTable for "high"
 * severity rows so the report page has a single visual language.
 */
function ReviewBadge({ label }: { label: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold"
      style={{
        backgroundColor: AMBER.bg,
        color: AMBER.text,
        borderColor: AMBER.border,
      }}
    >
      ⚠ {label}
    </span>
  );
}

/* ---------- grouping helpers ---------- */

function groupByType(
  commitments: FinancialCommitment[],
): Record<CommitmentType, FinancialCommitment[]> {
  const groups: Record<CommitmentType, FinancialCommitment[]> = {
    contract_value: [],
    bond: [],
    liquidated_damages: [],
    payment_milestone: [],
    retention: [],
    advance_payment: [],
  };
  for (const c of commitments) {
    groups[c.commitment_type].push(c);
  }
  return groups;
}

/* ---------- component ---------- */

export default function FinancialSummaryCard({
  tenderId,
}: FinancialSummaryCardProps) {
  const {
    data: commitments,
    isLoading,
    isError,
    error,
  } = useQuery<FinancialCommitment[]>({
    queryKey: ["financial", tenderId],
    queryFn: () => getFinancialCommitments(tenderId),
  });

  if (isLoading) {
    return <FinancialSummarySkeleton />;
  }

  if (isError) {
    // 404 is never thrown from getFinancialCommitments (it returns []), so
    // any error here is either a real API failure or an AuthError.
    const message =
      error instanceof AuthError
        ? "Authentication failed. Check your API key."
        : error instanceof ApiError
          ? `Failed to load financial commitments (HTTP ${error.status}).`
          : "Failed to load financial commitments.";
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

  if (!commitments || commitments.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Financial Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{
              backgroundColor: AMBER.bg,
              borderColor: AMBER.border,
              color: AMBER.text,
            }}
          >
            <Info className="mt-0.5 size-4 shrink-0" />
            <p>No financial commitments identified in this tender.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const groups = groupByType(commitments);
  const needsReviewCount = commitments.filter((c) => c.needs_review).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Financial Summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <SectionA contract={groups.contract_value[0]} />
        <SectionB bonds={groups.bond} />
        <SectionC ld={groups.liquidated_damages[0]} />
        <SectionD milestones={groups.payment_milestone} />
        <SectionE retention={groups.retention[0]} />
        <SectionF advance={groups.advance_payment[0]} />
        {needsReviewCount > 0 ? (
          <NeedsReviewBanner count={needsReviewCount} />
        ) : null}
      </CardContent>
    </Card>
  );
}

/* ---------- Section A: Contract Value (always renders) ---------- */

function SectionA({ contract }: { contract: FinancialCommitment | undefined }) {
  return (
    <section>
      <SectionHeader title="A — Contract Value" />
      {contract ? (
        <ContractValueDisplay commitment={contract} />
      ) : (
        <p className="text-sm text-muted-foreground">
          Contract value not stated
        </p>
      )}
    </section>
  );
}

function ContractValueDisplay({
  commitment,
}: {
  commitment: FinancialCommitment;
}) {
  const { text, showReviewBadge } = formatAmount(
    commitment.amount_value,
    commitment.amount_currency,
    commitment.needs_review,
  );

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-bold tabular-nums tracking-tight text-slate-900">
          {text}
        </span>
        {showReviewBadge ? (
          <ReviewBadge label="Currency requires review" />
        ) : null}
      </div>
      {commitment.description ? (
        <p className="text-xs text-muted-foreground">{commitment.description}</p>
      ) : null}
      {commitment.source_chunk_index !== null ? (
        <p className="text-xs text-slate-400">
          Source: chunk #{commitment.source_chunk_index}
        </p>
      ) : null}
    </div>
  );
}

/* ---------- Section B: Performance Bonds (table) ---------- */

function SectionB({ bonds }: { bonds: FinancialCommitment[] }) {
  if (bonds.length === 0) return null;
  return (
    <section>
      <SectionHeader title="B — Performance Bonds" />
      <div className="overflow-hidden rounded-md border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-600">
            <tr>
              <th className="px-3 py-2 text-left font-semibold">Type</th>
              <th className="px-3 py-2 text-right font-semibold">Amount</th>
              <th className="px-3 py-2 text-right font-semibold">
                % of Contract
              </th>
              <th className="px-3 py-2 text-left font-semibold">Conditions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {bonds.map((b) => (
              <BondRow key={b.id} bond={b} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BondRow({ bond }: { bond: FinancialCommitment }) {
  const { text, showReviewBadge } = formatAmount(
    bond.amount_value,
    bond.amount_currency,
    bond.needs_review,
  );
  const percentage =
    bond.percentage !== null ? `${bond.percentage}%` : "—";
  const isHighlighted = bond.needs_review;

  return (
    <tr
      style={
        isHighlighted
          ? {
              backgroundColor: AMBER.bg,
            }
          : undefined
      }
      title={
        isHighlighted
          ? "Currency or amount requires manual verification"
          : undefined
      }
    >
      <td className="px-3 py-3 align-top">
        <span className="text-sm font-medium text-slate-800">
          {bond.description}
        </span>
      </td>
      <td className="px-3 py-3 text-right align-top">
        <div className="flex flex-col items-end gap-1">
          <span className="tabular-nums font-medium text-slate-800">
            {text}
          </span>
          {showReviewBadge ? (
            <ReviewBadge label="Requires review" />
          ) : null}
        </div>
      </td>
      <td className="px-3 py-3 text-right align-top tabular-nums text-slate-700">
        {percentage}
      </td>
      <td className="px-3 py-3 align-top text-sm text-slate-600">
        {bond.description}
      </td>
    </tr>
  );
}

/* ---------- Section C: Liquidated Damages ---------- */

function SectionC({ ld }: { ld: FinancialCommitment | undefined }) {
  if (!ld) return null;
  return (
    <section>
      <SectionHeader title="C — Liquidated Damages" />
      <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
        <LiquidatedDamagesDisplay commitment={ld} />
      </div>
    </section>
  );
}

function LiquidatedDamagesDisplay({
  commitment,
}: {
  commitment: FinancialCommitment;
}) {
  const { text: rateText, showReviewBadge: rateNeedsReview } = formatAmount(
    commitment.amount_value,
    commitment.amount_currency,
    commitment.needs_review,
  );
  const period = extractPeriod(commitment.description) ?? "per period";

  // The single DB row stores the RATE in amount_value and the cap % in
  // percentage. There is no dedicated cap-amount column, so we render
  // the cap as a percentage of contract value when `percentage` is set.
  const capLine = renderCapLine(commitment);

  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-semibold text-slate-700">Rate:</span>
        <span className="tabular-nums text-slate-900">
          {rateText} {period}
        </span>
        {rateNeedsReview ? <ReviewBadge label="Requires review" /> : null}
      </div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-semibold text-slate-700">Cap:</span>
        <span className="text-slate-900">{capLine}</span>
      </div>
      {commitment.description ? (
        <p className="text-xs text-muted-foreground">
          {stripPeriod(commitment.description)}
        </p>
      ) : null}
      {commitment.source_chunk_index !== null ? (
        <p className="text-xs text-slate-400">
          Source: chunk #{commitment.source_chunk_index}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The DB row has no dedicated cap-amount column. We render the cap as a
 * percentage of contract value when `percentage` is set, otherwise as
 * "Not specified". This matches the slice spec fallback rule.
 */
function renderCapLine(commitment: FinancialCommitment): string {
  if (commitment.percentage !== null) {
    return `${commitment.percentage}% of contract value`;
  }
  return "Not specified";
}

/**
 * Try to pull a "per day" / "per week" phrase out of the description
 * text. Returns null if nothing recognisable is found so the caller can
 * fall back to a generic "per period".
 */
function extractPeriod(description: string): string | null {
  const match = description.match(/per\s+(day|week|month|hour)/i);
  return match ? match[0] : null;
}

/** Remove the period phrase from a description so it doesn't repeat. */
function stripPeriod(description: string): string {
  return description
    .replace(/[,.;]?\s*per\s+(day|week|month|hour)\s*/i, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/* ---------- Section D: Payment Schedule (timeline) ---------- */

function SectionD({
  milestones,
}: {
  milestones: FinancialCommitment[];
}) {
  if (milestones.length === 0) return null;
  return (
    <section>
      <SectionHeader title="D — Payment Schedule" />
      <ol className="space-y-2">
        {milestones.map((m) => (
          <li key={m.id}>
            <MilestoneCard milestone={m} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function MilestoneCard({ milestone }: { milestone: FinancialCommitment }) {
  const trigger = extractTrigger(milestone.description);
  const label = stripTrigger(milestone.description) || milestone.description;
  const rightSide = renderMilestoneAmount(milestone);

  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-slate-200 bg-white p-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-slate-800">{label}</p>
        <p className="mt-0.5 text-xs italic text-muted-foreground">
          {trigger ? `Trigger: ${trigger}` : "Trigger: not specified"}
        </p>
      </div>
      <div className="shrink-0 text-right">
        {rightSide.kind === "review" ? (
          <div className="flex flex-col items-end gap-1">
            <span className="tabular-nums text-sm text-slate-800">
              {rightSide.text}
            </span>
            <ReviewBadge label="Requires review" />
          </div>
        ) : (
          <span className="tabular-nums text-sm font-medium text-slate-800">
            {rightSide.text}
          </span>
        )}
      </div>
    </div>
  );
}

type MilestoneAmount =
  | { kind: "text"; text: string }
  | { kind: "review"; text: string };

function renderMilestoneAmount(
  m: FinancialCommitment,
): MilestoneAmount {
  if (m.amount_value !== null) {
    const { text, showReviewBadge } = formatAmount(
      m.amount_value,
      m.amount_currency,
      m.needs_review,
    );
    return showReviewBadge
      ? { kind: "review", text }
      : { kind: "text", text };
  }
  if (m.percentage !== null) {
    return {
      kind: "text",
      text: `${m.percentage}% of contract value`,
    };
  }
  return { kind: "text", text: "Amount not specified" };
}

/**
 * The DB description field holds the full milestone text in a loose
 * "Name — amount, upon trigger" shape. The "trigger event" is the
 * sentence fragment starting with the keyword "upon", "on", "after",
 * or "at" — e.g. "upon project handover". Returns null when no clear
 * trigger can be located.
 */
function extractTrigger(description: string): string | null {
  const uponMatch = description.match(
    /\b(?:upon|on|after|at)\s+[^.!?]+/i,
  );
  return uponMatch ? uponMatch[0].trim() : null;
}

/**
 * Isolate the milestone label by stripping the trigger phrase and any
 * trailing separator. If the description contains an em-dash / dash /
 * colon separator, the label is the part before the first separator.
 * Otherwise we trim the trigger phrase from the end of the string.
 */
function stripTrigger(description: string): string {
  const dashSplit = description.split(/\s+[—\-:]\s+/);
  if (dashSplit.length > 1) {
    return dashSplit[0].trim();
  }
  return description
    .replace(/[,.;]?\s*\b(?:upon|on|after|at)\s+[^.!?]+$/i, "")
    .trim();
}

/* ---------- Section E: Retention ---------- */

function SectionE({
  retention,
}: {
  retention: FinancialCommitment | undefined;
}) {
  if (!retention) return null;
  return (
    <section>
      <SectionHeader title="E — Retention" />
      <p className="text-sm text-slate-800">
        {retention.percentage !== null
          ? `Retention: ${retention.percentage}% of contract value`
          : "Retention: not specified"}
      </p>
      {retention.description ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {retention.description}
        </p>
      ) : null}
    </section>
  );
}

/* ---------- Section F: Advance Payment ---------- */

function SectionF({
  advance,
}: {
  advance: FinancialCommitment | undefined;
}) {
  if (!advance) return null;
  const { text, showReviewBadge } = formatAmount(
    advance.amount_value,
    advance.amount_currency,
    advance.needs_review,
  );
  return (
    <section>
      <SectionHeader title="F — Advance Payment" />
      <div className="flex flex-wrap items-center gap-2">
        <span className="tabular-nums text-sm font-medium text-slate-800">
          {text}
        </span>
        {showReviewBadge ? <ReviewBadge label="Requires review" /> : null}
      </div>
      {advance.description ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {advance.description}
        </p>
      ) : null}
    </section>
  );
}

/* ---------- Needs-review summary banner ---------- */

function NeedsReviewBanner({ count }: { count: number }) {
  return (
    <div
      className="flex items-start gap-2 rounded-lg border p-3 text-sm"
      style={{
        backgroundColor: AMBER.bg,
        borderColor: AMBER.border,
        color: AMBER.text,
      }}
      role="status"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <p>
        ⚠ {count} item{count === 1 ? "" : "s"} require
        {count === 1 ? "s" : ""} manual currency verification before using
        these figures in a bid decision.
      </p>
    </div>
  );
}

/* ---------- shared section header ---------- */

function SectionHeader({ title }: { title: string }) {
  return (
    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
      {title}
    </h3>
  );
}

/* ---------- skeleton ---------- */

function FinancialSummarySkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Financial Summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-10 w-48" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-24 w-full" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-16 w-full" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-4 w-48" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-4 w-48" />
        </div>
      </CardContent>
    </Card>
  );
}
