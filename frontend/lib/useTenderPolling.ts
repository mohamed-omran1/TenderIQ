"use client";

/**
 * Polling hook for tender ingestion status.
 *
 * REQ-001 Main Flow step 10: the client polls GET /tenders/{id} to detect
 * "ready" or "failed". This hook owns that loop so the upload component stays
 * declarative — it just renders the returned `phase`.
 *
 * States map to the REQ-001 acceptance criterion (distinct UI for uploading /
 * processing / ready / each failure):
 *   - "idle"        — no upload yet
 *   - "uploading"   — POST in flight
 *   - "processing"  — 202 received, polling until ready/failed
 *   - "ready"       — terminal success
 *   - "failed"      — terminal failure (carries the backend's error_reason)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_POLL_INTERVAL_MS,
  DEFAULT_POLL_TIMEOUT_MS,
  getTender,
} from "./api";
import type { TenderDetailResponse, UploadError } from "./types";

export type PollPhase =
  | "idle"
  | "uploading"
  | "processing"
  | "ready"
  | "failed";

export interface PollingState {
  phase: PollPhase;
  tender: TenderDetailResponse | null;
  error: UploadError | null;
}

interface StartArgs {
  apiKey: string;
  tenderId: string;
}

export function useTenderPolling() {
  const [state, setState] = useState<PollingState>({
    phase: "idle",
    tender: null,
    error: null,
  });

  // Track the active poll so we can cancel it on unmount / new upload.
  const abortRef = useRef<AbortController | null>(null);
  // Hard-deadline ref so a stuck "processing" can't poll forever.
  const deadlineRef = useRef<number>(0);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const start = useCallback(({ apiKey, tenderId }: StartArgs) => {
    // Cancel any prior poll before starting a new one.
    stop();
    const controller = new AbortController();
    abortRef.current = controller;
    deadlineRef.current = Date.now() + DEFAULT_POLL_TIMEOUT_MS;

    setState({ phase: "processing", tender: null, error: null });

    const poll = async () => {
      while (!controller.signal.aborted) {
        if (Date.now() > deadlineRef.current) {
          setState((s) => ({
            ...s,
            phase: "failed",
            error: {
              kind: "ingestion_failed",
              reason: "Ingestion timed out — no status change within the deadline.",
            },
          }));
          return;
        }

        const tender = await getTender(tenderId, {
          apiKey,
          signal: controller.signal,
        });

        if (tender) {
          if (tender.status === "ready") {
            setState({ phase: "ready", tender, error: null });
            return;
          }
          if (tender.status === "failed") {
            setState({
              phase: "failed",
              tender,
              error: {
                kind: "ingestion_failed",
                reason: tender.error_reason,
              },
            });
            return;
          }
          // still uploading/processing — keep the freshest row for the UI
          setState((s) => ({ ...s, tender }));
        }

        await new Promise((resolve) =>
          setTimeout(resolve, DEFAULT_POLL_INTERVAL_MS),
        );
      }
    };

    void poll();
  }, [stop]);

  // Cancel on unmount — never leave a dangling setTimeout/fetch loop.
  useEffect(() => stop, [stop]);

  return { state, start, stop };
}
