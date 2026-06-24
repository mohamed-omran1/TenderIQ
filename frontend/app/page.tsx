import { redirect } from "next/navigation";

/**
 * Root route — for MVP the only flow is upload, so send users straight there.
 * The full PRD §8.1 route map (Dashboard, /tenders/[id], /report, /analytics)
 * lands in later slices.
 */
export default function Home() {
  redirect("/upload");
}
