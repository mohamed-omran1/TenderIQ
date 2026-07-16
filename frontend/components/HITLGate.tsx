"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { getRunStatus } from "@/lib/api/analysis";
import {
  approveRun,
  getHITLOverride,
  overrideRun,
  ApiError,
  AuthError,
  ConflictError,
  ValidationError,
  type HITLOverride,
  type HITLResponse,
} from "@/lib/api/hitl";
import { getFinancialCommitments } from "@/lib/api/financial";
import { useRunStream } from "@/hooks/useRunStream";

type ScoreBand = "red" | "amber" | "green";
type PaletteEntry = { bg: string; text: string; label: string };

const BAND_PALETTE: Record<ScoreBand, PaletteEntry> = {
  red: { bg: "#FEE2E2", text: "#B91C1C", label: "red" },
  amber: { bg: "#FEF3C7", text: "#92400E", label: "amber" },
  green: { bg: "#D1FAE5", text: "#065F46", label: "green" },
};

function bandFor(percentage: number): ScoreBand {
  if (percentage <= 39) return "red";
  if (percentage <= 69) return "amber";
  return "green";
}

interface HITLGateProps {
  tenderId: string;
  currentScore: number;
  runState: string;
  onApproved: () => void;
}

export default function HITLGate({
  tenderId,
  currentScore,
  runState,
  onApproved,
}: HITLGateProps) {
  const [isAdjusting, setIsAdjusting] = useState(false);
  const [sliderValue, setSliderValue] = useState(currentScore);
  const [justification, setJustification] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [hitlResult, setHitlResult] = useState<HITLResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [justificationError, setJustificationError] = useState<string | null>(
    null,
  );

  const { latestEvent, connectionState } = useRunStream(
    runState === "resuming" ? tenderId : null,
  );

  const handleToggleAdjust = useCallback(() => {
    setIsAdjusting((prev) => {
      const next = !prev;
      if (next) {
        setSliderValue(currentScore);
        setJustification("");
        setJustificationError(null);
      } else {
        setSliderValue(currentScore);
        setJustification("");
        setJustificationError(null);
      }
      return next;
    });
  }, [currentScore]);

  const handleSliderChange = useCallback(
    (value: number | readonly number[]) => {
      const v = Array.isArray(value) ? value[0] : value;
      setSliderValue(v);
      if (v !== currentScore && justificationError) {
        setJustificationError(null);
      }
    },
    [currentScore, justificationError],
  );

  const handleJustificationChange = useCallback(
    (value: string) => {
      setJustification(value);
      if (value.length >= 10 && justificationError) {
        setJustificationError(null);
      }
    },
    [justificationError],
  );

  const handleSubmit = useCallback(async () => {
    if (isAdjusting && sliderValue !== currentScore) {
      if (justification.length < 10) {
        setJustificationError("Justification required (minimum 10 characters)");
        return;
      }
    }

    setIsSubmitting(true);
    setSubmitError(null);
    setJustificationError(null);

    try {
      let result: HITLResponse;

      if (isAdjusting && sliderValue !== currentScore) {
        result = await overrideRun(tenderId, sliderValue, justification);
      } else {
        const note =
          justification.trim().length > 0 ? justification : undefined;
        result = await approveRun(tenderId, note);
      }

      setHitlResult(result);
      setSubmitted(true);
    } catch (err) {
      if (err instanceof ConflictError) {
        setSubmitError(err.message);
      } else if (err instanceof AuthError) {
        setSubmitError("Authentication failed. Check your API key.");
      } else if (err instanceof ValidationError) {
        setSubmitError(err.message);
      } else if (err instanceof ApiError) {
        setSubmitError(`Request failed (HTTP ${err.status}).`);
      } else {
        setSubmitError("An unexpected error occurred.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }, [
    isAdjusting,
    sliderValue,
    currentScore,
    justification,
    tenderId,
  ]);

  const { data: pollingStatus } = useQuery({
    queryKey: ["hitl-poll", tenderId],
    queryFn: () => getRunStatus(tenderId),
    enabled: submitted,
    refetchInterval:
      connectionState === "error" && runState === "resuming"
        ? 3000
        : false,
  });

  const { data: financialData } = useQuery({
    queryKey: ["financial", tenderId],
    queryFn: () => getFinancialCommitments(tenderId),
  });

  const needsReviewCount =
    financialData?.filter((c) => c.needs_review).length ?? 0;

  useEffect(() => {
    if (latestEvent?.event_type === "complete") {
      onApproved();
    }
  }, [latestEvent, onApproved]);

  const polledComplete = pollingStatus?.state === "complete";

  useEffect(() => {
    if (polledComplete && submitted) {
      setSubmitted(false);
      onApproved();
    }
  }, [polledComplete, submitted, onApproved]);

  if (runState === "complete") {
    return <State3Complete hitlResult={hitlResult} tenderId={tenderId} />;
  }

  if (submitted || runState === "resuming") {
    return <State2Resuming />;
  }

  const isOverridden = isAdjusting && sliderValue !== currentScore;
  const buttonLabel = isAdjusting
    ? isOverridden
      ? `Override Score to ${sliderValue}`
      : "Approve with Note"
    : "Approve AI Score";

  return (
    <Card
      className="border-2"
      style={{ borderColor: "#FCD34D", backgroundColor: "#FFFBEB" }}
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-amber-800">
          <AlertTriangle className="size-5" />
          Analyst Review Required
        </CardTitle>
        <CardDescription className="text-amber-700">
          Review the findings above before generating the final report. You may
          approve the AI score or adjust it based on your expert judgment.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {needsReviewCount > 0 && (
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{
              backgroundColor: "#FEF3C7",
              borderColor: "#FCD34D",
              color: "#92400E",
            }}
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <p>
              {needsReviewCount} financial item(s) have unverified currencies
              and require manual review before approval. See the Financial
              Summary above for details.
            </p>
          </div>
        )}

        <ScoreDisplay score={isAdjusting ? sliderValue : currentScore} />

        <div className="flex items-center gap-2">
          <input
            id="adjust-toggle"
            type="checkbox"
            checked={isAdjusting}
            onChange={handleToggleAdjust}
            disabled={isSubmitting}
            className="size-4 rounded border-amber-400 text-primary accent-primary"
          />
          <label
            htmlFor="adjust-toggle"
            className="text-sm font-medium text-slate-700 cursor-pointer select-none"
          >
            Adjust score
          </label>
        </div>

        {isAdjusting && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Slider
                value={[sliderValue]}
                onValueChange={handleSliderChange}
                min={0}
                max={100}
                step={1}
                disabled={isSubmitting}
                aria-label="Adjust feasibility score"
              />
              <LiveScoreDisplay score={sliderValue} />
            </div>

            <div className="space-y-1.5">
              <Textarea
                placeholder="Explain why you are adjusting the score (required when changing the score)..."
                value={justification}
                onChange={(e) => handleJustificationChange(e.target.value)}
                disabled={isSubmitting}
                className={cn(
                  justificationError && "border-destructive ring-2 ring-destructive/20",
                )}
              />
              <div className="flex items-center justify-between">
                {justificationError ? (
                  <p className="text-xs text-destructive">
                    {justificationError}
                  </p>
                ) : (
                  <span className="text-xs text-slate-400">
                    {justification.length} characters
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {!isAdjusting && (
          <div className="space-y-1.5">
            <Textarea
              placeholder="Add a note (optional)..."
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              disabled={isSubmitting}
              className="min-h-20"
            />
          </div>
        )}

        {submitError ? (
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{
              backgroundColor: "#FEE2E2",
              borderColor: "#FCA5A5",
              color: "#B91C1C",
            }}
            role="alert"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <p>{submitError}</p>
          </div>
        ) : null}
      </CardContent>

      <CardFooter>
        <Button
          onClick={handleSubmit}
          disabled={isSubmitting}
          className="w-full"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Processing...
            </>
          ) : (
            buttonLabel
          )}
        </Button>
      </CardFooter>
    </Card>
  );
}

function ScoreDisplay({ score }: { score: number }) {
  const band = bandFor(score);
  const palette = BAND_PALETTE[band];

  return (
    <div className="flex flex-col items-center gap-1 py-1">
      <p className="text-xs font-medium text-slate-500">AI Feasibility Score</p>
      <div
        className="flex size-20 items-center justify-center rounded-full ring-4 ring-white"
        style={{ backgroundColor: palette.bg }}
      >
        <span
          className="text-2xl font-bold tabular-nums"
          style={{ color: palette.text }}
        >
          {Math.round(score)}
        </span>
      </div>
      <p className="text-xs text-slate-400">/ 100</p>
    </div>
  );
}

function LiveScoreDisplay({ score }: { score: number }) {
  const band = bandFor(score);
  const palette = BAND_PALETTE[band];

  return (
    <p
      className="text-sm font-semibold tabular-nums"
      style={{ color: palette.text }}
    >
      New score: {Math.round(score)} / 100
    </p>
  );
}

function State2Resuming() {
  return (
    <Card
      className="border-2"
      style={{ borderColor: "#93C5FD", backgroundColor: "#EFF6FF" }}
    >
      <CardContent className="flex flex-col items-center gap-3 py-8">
        <Loader2 className="size-8 animate-spin text-blue-600" />
        <div className="text-center">
          <p className="text-lg font-semibold text-blue-900">
            Generating Report...
          </p>
          <p className="mt-1 text-sm text-blue-700">
            The final report is being assembled. This usually takes 30–60
            seconds.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function State3Complete({
  hitlResult,
  tenderId,
}: {
  hitlResult: HITLResponse | null;
  tenderId: string;
}) {
  const { data: overrideData } = useQuery({
    queryKey: ["hitl-override", tenderId],
    queryFn: () => getHITLOverride(tenderId),
  });

  return (
    <Card
      className="border-2"
      style={{ borderColor: "#6EE7B7", backgroundColor: "#ECFDF5" }}
    >
      <CardContent className="flex flex-col items-center gap-3 py-6">
        <div className="flex size-10 items-center justify-center rounded-full bg-green-100">
          <svg
            className="size-5 text-green-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <div className="text-center">
          <p className="text-lg font-semibold text-green-900">
            Report Generated
          </p>
          <p className="mt-1 text-sm text-green-700">
            The Go/No-Go report has been generated.
          </p>
        </div>

        {overrideData == null || overrideData.action === "approved" ? (
          <p className="text-sm text-slate-400">
            Score approved as-is: {overrideData?.original_score ?? hitlResult?.original_score ?? "—"} / 100
          </p>
        ) : (
          <div className="w-full space-y-1 rounded-lg border border-green-200 bg-white px-4 py-3 text-sm">
            <p className="font-medium text-slate-700">
              Score adjusted by analyst review:
            </p>
            <p className="text-slate-500">
              AI Score:{" "}
              <span className="line-through">
                {overrideData.original_score}
              </span>{" "}
              / 100
            </p>
            <p>
              Final Score:{" "}
              <span className="font-bold text-green-700">
                {overrideData.overridden_score}
              </span>{" "}
              / 100
            </p>
            <p className="text-xs text-slate-400">
              Analyst override recorded in audit log
            </p>
          </div>
        )}

        <Button
          onClick={() =>
            (window.location.href = `/tenders/${tenderId}/report/full`)
          }
          variant="default"
          className="mt-2"
        >
          View Full Report
        </Button>
      </CardContent>
    </Card>
  );
}
