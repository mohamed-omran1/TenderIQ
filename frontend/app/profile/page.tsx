"use client";

/**
 * /profile page wrapper (REQ-002 Slice 3).
 *
 * Provides a local TanStack Query QueryClient for the company-profile form so
 * the page can fetch and mutate independently without needing to touch the
 * root layout.
 */

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import CompanyProfileForm from "@/components/CompanyProfileForm";

export default function ProfilePage() {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
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
            Company Profile
          </h1>
          <p className="text-muted-foreground">
            This profile is used by the Feasibility Scorer to evaluate tender
            fit against your company&apos;s capabilities.
          </p>
        </div>
        <CompanyProfileForm />
      </main>
    </QueryClientProvider>
  );
}
