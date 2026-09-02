import { AppShell } from "@/components/app-shell";
import { getDashboardData } from "@/lib/data";
import { deriveSeasonContext } from "@/lib/season";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const dashboard = await getDashboardData().catch(() => null);
  const season = dashboard ? deriveSeasonContext(dashboard.bootstrap.events, { finalizedGw: dashboard.gameweek }) : null;
  return <AppShell context={{
    latestSnapshotGw: dashboard?.gameweek,
    requestedSnapshotGw: dashboard?.requestedGameweek,
    snapshotStatus: dashboard?.snapshotStatus,
    dataSource: dashboard?.dataStatus?.source,
    dataQuality: dashboard?.dataStatus?.quality,
    planningGw: season?.nextDeadlineGw ?? dashboard?.gameweek,
  }}>{children}</AppShell>;
}
