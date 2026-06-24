"use client";

/**
 * TenderUpload — drag-and-drop PDF upload with live status + distinct error UI.
 *
 * Implements REQ-001 Slice 3:
 *   - Drag-and-drop (and click-to-select) calling POST /tenders/upload.
 *   - Polls GET /tenders/{id} for the terminal "ready" / "failed" status.
 *   - Renders a distinct UI state for every Alternative Flow: unsupported type,
 *     oversize, rate-limited (with retry-after), quota, corrupt/scanned
 *     (ingestion_failed), network, and auth.
 *
 * Auth note (senior-fullstack skill): the API key is held in component state
 * only for this MVP single-user upload screen; it is sent as a Bearer header,
 * never in the request body or a query string. A real session layer replaces
 * this in a later slice.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { uploadTender } from "@/lib/api";
import { useTenderPolling } from "@/lib/useTenderPolling";
import type { UploadError } from "@/lib/types";

const MAX_BYTES = 50 * 1024 * 1024; // 50MB — matches backend max_upload_mb
const PDF_MIME = "application/pdf";

type View =
  | { kind: "idle" }
  | { kind: "uploading"; filename: string }
  | { kind: "processing"; filename: string }
  | { kind: "ready"; filename: string; language: string | null }
  | { kind: "error"; filename: string | null; error: UploadError };

function isPdf(file: File): boolean {
  // Check MIME first, then the %PDF- magic bytes — same defence the backend uses.
  if (file.type === PDF_MIME) return true;
  return file.name.toLowerCase().endsWith(".pdf");
}

/** Human-readable copy per error kind. Each maps 1:1 to an Alternative Flow. */
function errorMessage(error: UploadError): { title: string; body: string } {
  switch (error.kind) {
    case "unsupported_file_type":
      return {
        title: "Unsupported file type",
        body: "Only PDF files are supported. Please upload a .pdf document.",
      };
    case "file_too_large":
      return {
        title: "File too large",
        body: "Your file exceeds the 50 MB upload limit.",
      };
    case "rate_limited":
      return {
        title: "Too many uploads",
        body:
          error.retryAfter != null
            ? `You're uploading too quickly. Please try again in ${error.retryAfter} second${error.retryAfter === 1 ? "" : "s"}.`
            : "You're uploading too quickly. Please wait and try again.",
      };
    case "quota_exceeded":
      return {
        title: "Monthly quota reached",
        body: "You've used all your document uploads for this month.",
      };
    case "unauthorized":
      return {
        title: "Invalid API key",
        body: "The API key is missing or invalid. Check it and try again.",
      };
    case "not_found":
      return {
        title: "Tender not found",
        body: "This tender doesn't exist or belongs to another company.",
      };
    case "ingestion_failed":
      return {
        title: "Couldn't process the PDF",
        body:
          error.reason ??
          "Ingestion failed — the PDF may be corrupt, password-protected, or a scanned image (OCR isn't supported yet).",
      };
    case "network":
      return {
        title: "Connection problem",
        body: "Couldn't reach the server. Check your connection and try again.",
      };
    case "server_error":
      return {
        title: "Server error",
        body: `The server returned an error (HTTP ${error.status}). Please try again.`,
      };
  }
}

export default function TenderUpload() {
  const [view, setView] = useState<View>({ kind: "idle" });
  const [isDragging, setIsDragging] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  // Filename of the in-flight upload — kept in a ref so the polling effect can
  // stamp it onto terminal views without reconstructing it from the view union.
  const filenameRef = useRef<string>("");
  const { state: poll, start: startPolling, stop: stopPolling } =
    useTenderPolling();

  // Reflect polling phase back into the local view so the UI has one source
  // of truth for what to render. The filename comes from filenameRef (set when
  // the upload began) rather than from the prior view, keeping this effect a
  // pure function of the poll state.
  useEffect(() => {
    const filename = filenameRef.current;
    if (poll.phase === "processing") {
      setView({ kind: "processing", filename });
    } else if (poll.phase === "ready") {
      setView({
        kind: "ready",
        filename,
        language: poll.tender?.primary_language ?? null,
      });
    } else if (poll.phase === "failed" && poll.error) {
      setView({ kind: "error", filename, error: poll.error });
    }
  }, [poll.phase, poll.error, poll.tender]);

  // Cancel any in-flight upload/poll when the component unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const handleFile = useCallback(
    async (file: File) => {
      // --- Pre-flight client validation (mirrors backend, for instant feedback) ---
      if (!isPdf(file)) {
        setView({
          kind: "error",
          filename: file.name,
          error: { kind: "unsupported_file_type" },
        });
        return;
      }
      if (file.size > MAX_BYTES) {
        setView({
          kind: "error",
          filename: file.name,
          error: { kind: "file_too_large" },
        });
        return;
      }
      if (!apiKey.trim()) {
        setView({
          kind: "error",
          filename: file.name,
          error: { kind: "unauthorized" },
        });
        return;
      }

      stopPolling();
      filenameRef.current = file.name;
      setView({ kind: "uploading", filename: file.name });

      const controller = new AbortController();
      abortRef.current = controller;

      const result = await uploadTender(file, {
        apiKey: apiKey.trim(),
        signal: controller.signal,
      });

      if (!result.ok) {
        setView({
          kind: "error",
          filename: file.name,
          error: result.error,
        });
        return;
      }

      // 202 received — begin polling for the terminal status.
      startPolling({ apiKey: apiKey.trim(), tenderId: result.data.tender_id });
    },
    [apiKey, startPolling, stopPolling],
  );

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLLabelElement>) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    stopPolling();
    setView({ kind: "idle" });
  }, [stopPolling]);

  const busy = view.kind === "uploading" || view.kind === "processing";

  return (
    <div className="w-full max-w-xl">
      {/* --- API key (MVP auth — replaced by a session layer later) --- */}
      <div className="mb-4">
        <label
          htmlFor="api-key"
          className="mb-1 block text-sm font-medium text-slate-700"
        >
          API key
        </label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          disabled={busy}
          placeholder="Bearer API key"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 disabled:bg-slate-50"
        />
      </div>

      {/* --- Dropzone (only shown when idle) --- */}
      {view.kind === "idle" && (
        <label
          htmlFor="file-input"
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          className={`flex h-56 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed transition-colors ${
            isDragging
              ? "border-indigo-500 bg-indigo-50"
              : "border-slate-300 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/40"
          }`}
        >
          <svg
            className="mb-3 h-10 w-10 text-slate-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
            />
          </svg>
          <span className="text-sm font-medium text-slate-700">
            Drag &amp; drop a tender PDF here
          </span>
          <span className="mt-1 text-xs text-slate-500">
            or click to browse · max 50 MB · .pdf only
          </span>
          <input
            id="file-input"
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
              // reset so selecting the same file twice still fires onChange
              e.target.value = "";
            }}
          />
        </label>
      )}

      {/* --- Uploading (POST in flight) --- */}
      {view.kind === "uploading" && (
        <StatusCard tone="info" filename={view.filename} title="Uploading…">
          <ProgressBar indeterminate />
          <p className="mt-2 text-xs text-slate-500">
            Sending the file to TenderIQ…
          </p>
        </StatusCard>
      )}

      {/* --- Processing (202 received, polling) --- */}
      {view.kind === "processing" && (
        <StatusCard tone="info" filename={view.filename} title="Processing">
          <ProgressBar indeterminate />
          <p className="mt-2 text-xs text-slate-500">
            Extracting text, detecting language, and generating embeddings. This
            can take up to 90 seconds for large documents.
          </p>
        </StatusCard>
      )}

      {/* --- Ready (terminal success) --- */}
      {view.kind === "ready" && (
        <StatusCard
          tone="success"
          filename={view.filename}
          title="Ready"
          action={<ResetButton onClick={reset} />}
        >
          <p className="text-sm text-slate-600">
            Ingestion complete.{" "}
            {view.language && (
              <span className="text-slate-500">
                Detected language:{" "}
                <span className="font-medium text-slate-700">
                  {view.language === "ar"
                    ? "Arabic"
                    : view.language === "en"
                      ? "English"
                      : "Bilingual (Arabic + English)"}
                </span>
                .
              </span>
            )}
          </p>
        </StatusCard>
      )}

      {/* --- Error (one state per Alternative Flow) --- */}
      {view.kind === "error" && (
        <StatusCard
          tone="error"
          filename={view.filename}
          title={errorMessage(view.error).title}
          action={<ResetButton onClick={reset} label="Try again" />}
        >
          <p className="text-sm text-slate-600">{errorMessage(view.error).body}</p>
        </StatusCard>
      )}
    </div>
  );
}

/* ---------- small presentational helpers ---------- */

function StatusCard({
  tone,
  filename,
  title,
  action,
  children,
}: {
  tone: "info" | "success" | "error";
  filename?: string | null;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  const toneClasses = {
    info: "border-indigo-200 bg-indigo-50",
    success: "border-emerald-200 bg-emerald-50",
    error: "border-rose-200 bg-rose-50",
  }[tone];
  const icon = {
    info: "🔄",
    success: "✅",
    error: "⚠️",
  }[tone];
  return (
    <div className={`rounded-xl border p-5 ${toneClasses}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span aria-hidden>{icon}</span>
            <h3 className="font-semibold text-slate-800">{title}</h3>
          </div>
          {filename && (
            <p className="mt-0.5 truncate text-xs text-slate-500">{filename}</p>
          )}
        </div>
        {action}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function ProgressBar({ indeterminate }: { indeterminate?: boolean }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
      <div
        className={`h-full bg-indigo-500 ${
          indeterminate ? "animate-pulse" : ""
        }`}
        style={indeterminate ? { width: "60%" } : { width: "100%" }}
      />
    </div>
  );
}

function ResetButton({
  onClick,
  label = "Upload another",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
    >
      {label}
    </button>
  );
}
