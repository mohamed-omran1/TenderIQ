"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import AgentStreamViewer from "@/components/AgentStreamViewer";

export default function TenderAnalysisPage() {
  const params = useParams<{ id: string }>();

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 0,
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <main className="mx-auto max-w-3xl px-4 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">
            Tender Analysis
          </h1>
        </div>
        <AgentStreamViewer tenderId={params.id} />
      </main>
    </QueryClientProvider>
  );
}
