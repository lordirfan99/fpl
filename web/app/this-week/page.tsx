import { getCompactCatalog } from "@/lib/data";
import { deriveSeasonContext } from "@/lib/season";
import { AppShell } from "@/components/app-shell";
import "@/app/decision-room.css";
import "@/app/league-decision.css";
import { ThisWeekStatic } from "@/components/this-week-static";

export const dynamic = "force-dynamic";
export default async function ThisWeekPage() {
  const catalog = await getCompactCatalog().catch(() => null);
  const finalizedGw = catalog?.events.filter(e => e.finished && e.data_checked).at(-1)?.id ?? 0;
  const season = catalog ? deriveSeasonContext(catalog.events, { finalizedGw }) : null;
  return <AppShell context={{ latestSnapshotGw: finalizedGw, planningGw: season?.nextDeadlineGw,
    dataSource: process.env.FPL_PUBLIC_DATA_URL ? "Scheduled public snapshots; not live scores" : undefined }}><div className="page-stack decision-home">
    <header><span className="evidence-label">YOUR DECISION ROOM</span><h1>This gameweek</h1><p>The whole decision on one page: transfers, captain, XI, bench, bank — plus the recorded league facts behind them.</p></header>
    <ThisWeekStatic />
  </div></AppShell>;
}
