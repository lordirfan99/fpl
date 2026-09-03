import { LeagueTable } from "@/components/league-table";
import { LeagueSwitcher, resolveLeague } from "@/components/league-switcher";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { getCompactCatalog, getLeagueManager, getLeagueSummary, MY_TEAM_ID } from "@/lib/data";
import { formatMYT } from "@/lib/format";

type Params = { league?: string; page?: string; q?: string; manager?: string };

export default async function LeaguePage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const selected = resolveLeague(params.league);
  const requestedPage = Math.max(1, Number(params.page) || 1);
  const data = await getLeagueSummary(selected.id, { page: requestedPage, query: params.q });
  const selectedManagerId = Number(params.manager) || 0;
  const [bootstrap, managerDetail] = await Promise.all([
    getCompactCatalog(),
    selectedManagerId ? getLeagueManager(selected.id, data.gameweek, selectedManagerId).catch(() => undefined) : undefined,
  ]);
  const myTeam = data.manager;
  return <div className="page-stack">
    <PageHeader eyebrow="LEAGUE INTELLIGENCE" title={selected.name} description={`${data.total.toLocaleString()} managers · server-paginated standings`} updated={formatMYT(data.meta?.snapshot_at)} />
    <LeagueSwitcher selected={selected.id} pathname="/league" />
    <section className="metric-grid"><MetricCard label="Managers" value={data.total.toLocaleString()} detail={`League ${selected.id}`} /><MetricCard label="Average GW" value={data.average_gameweek_points.toFixed(1)} /><MetricCard label="Your rank" value={myTeam ? `#${myTeam.league_rank}` : "—"} detail={myTeam ? `${myTeam.gw_points} points` : "Your team is not in this league"} tone={myTeam ? (myTeam.gw_points >= data.average_gameweek_points ? "positive" : "warning") : undefined} /><MetricCard label="Leader" value={`${data.leader?.gw_points ?? 0} pts`} detail={data.leader?.entry_name} /></section>
    <section className="surface table-surface"><div className="section-heading"><div><span>STANDINGS</span><h2>League table</h2></div><span className="section-chip">GW{data.gameweek}</span></div><LeagueTable managers={data.managers} selected={managerDetail} myTeamId={MY_TEAM_ID} bootstrap={bootstrap} gameweek={data.gameweek} leagueId={selected.id} page={data.page} pages={data.pages} filteredTotal={data.filtered_total} query={data.query} /></section>
  </div>;
}
