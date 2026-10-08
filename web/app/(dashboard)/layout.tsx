import { AppShell } from "@/components/app-shell";
import { getCompactCatalog } from "@/lib/data";
import { deriveSeasonContext } from "@/lib/season";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Navigation needs season dates, not thousands of managers and their squads.
  const catalog = await getCompactCatalog().catch(() => null);
  const finalizedGw = catalog?.events.filter(event => event.finished && event.data_checked).at(-1)?.id ?? 0;
  const season = catalog ? deriveSeasonContext(catalog.events, { finalizedGw }) : null;
  return <AppShell context={{
    latestSnapshotGw: finalizedGw,
    planningGw: season?.nextDeadlineGw,
  }}>{children}</AppShell>;
}
