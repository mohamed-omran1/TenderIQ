import type { NextConfig } from "next";

/**
 * Next.js config.
 *
 * The frontend talks to the FastAPI backend over public HTTP at runtime
 * (NEXT_PUBLIC_API_BASE_URL). No rewrites — the API base is read directly by
 * the client so the same build works in dev (localhost:8000) and prod.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
