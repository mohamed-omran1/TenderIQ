import TenderUpload from "@/components/TenderUpload";

export const metadata = {
  title: "Upload Tender · TenderIQ",
  description: "Upload a tender PDF for ingestion and analysis.",
};

/**
 * /upload — the REQ-001 Slice 3 entry point.
 *
 * This page is a Server Component; all interactivity lives in the client
 * <TenderUpload /> component (drag-drop, upload, polling).
 */
export default function UploadPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4 py-12">
      <header className="mb-8 text-center">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">
          Upload a tender
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Drop a tender PDF and TenderIQ will extract, chunk, and embed it —
          ready for risk, feasibility, and financial analysis.
        </p>
      </header>

      <TenderUpload />
    </main>
  );
}
