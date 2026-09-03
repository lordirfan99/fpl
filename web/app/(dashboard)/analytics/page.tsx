import { LeagueSwitcher, resolveLeague } from "@/components/league-switcher";
import Link from "next/link";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { getLeagueData } from "@/lib/data";
import { analyzeElite } from "@/lib/elite";
import type { Manager } from "@/lib/types";
import { formatMYT } from "@/lib/format";

type Row = { label: string; count: number; percentage: number };

function formation(manager: Manager) {
  const starters = manager.squad.slice(0, 11);
  return ["DEF", "MID", "FWD"].map((position) => starters.filter((pick) => pick.position === position).length).join("-");
}

function grouped(values: string[], denominator: number): Row[] {
  const counts = new Map<string, number>(); values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return [...counts].map(([label, count]) => ({ label, count, percentage: count / Math.max(1, denominator) * 100 })).sort((a, b) => b.count - a.count);
}

function histogram(values: number[], step: number, prefix = ""): Row[] {
  if (!values.length) return [];
  const minimum = Math.floor(Math.min(...values) / step) * step, maximum = Math.ceil(Math.max(...values) / step) * step;
  const output: Row[] = [];
  for (let start = minimum; start <= maximum; start += step) { const count = values.filter((value) => value >= start && value < start + step).length; if (count) output.push({ label: `${prefix}${start.toFixed(prefix ? 0 : 0)}–${prefix}${(start + step - (prefix ? 0.1 : 1)).toFixed(prefix ? 1 : 0)}`, count, percentage: count / values.length * 100 }); }
  return output;
}

function Chart({ title, rows }: { title: string; rows: Row[] }) {
  const maximum = Math.max(1, ...rows.map((row) => row.count));
  return <section className="surface"><div className="section-heading"><div><span>LEAGUE BREAKDOWN</span><h2>{title}</h2></div></div><div className="distribution-list">{rows.map((row) => <article key={row.label}><div><strong>{row.label}</strong><span>{row.count} · {row.percentage.toFixed(1)}%</span></div><div className="distribution-track"><i style={{ width: `${row.count / maximum * 100}%` }} /></div></article>)}</div></section>;
}

export default async function AnalyticsPage({ searchParams }: { searchParams: Promise<{ league?: string; gw?: string }> }) {
  const params = await searchParams;
  const selected = resolveLeague(params.league), requestedGw = params.gw ? Math.max(1, Math.min(38, Number(params.gw) || 1)) : undefined, data = await getLeagueData(selected.id, requestedGw), elite = analyzeElite(data.managers);
  const formations = grouped(data.managers.map(formation), data.managers.length).slice(0, 8), captains = grouped(data.managers.map((manager) => manager.captain || "Unknown"), data.managers.length).slice(0, 10);
  const points = histogram(data.managers.map((manager) => manager.gw_points), 10), costs = histogram(data.managers.map((manager) => manager.squad_cost).filter(Boolean), 1, "£");
  const average = data.managers.reduce((sum, manager) => sum + manager.gw_points, 0) / data.managers.length;
  return <div className="page-stack"><PageHeader eyebrow={`LEAGUE ANALYTICS · GW${data.gameweek} ${data.liveProvisional ? "LIVE" : "SNAPSHOT"}`} title={`${selected.name} data lab`} description={data.liveProvisional ? "Live official FPL standings; final points and ranks may still change." : "Formation, points, value, captaincy and elite-vs-league ownership patterns from one captured gameweek."} updated={formatMYT(data.fetchedAt)} /><LeagueSwitcher selected={selected.id} pathname="/analytics" />
    <section className="execution-note"><div><strong>Snapshot, not a season trend</strong><p>This page explains the current captured league state. For weekly decisions, outcomes and lessons over time, use the <Link href="/journal">Season Journal</Link>.</p></div></section>
    <section className="metric-grid"><MetricCard label="Managers analyzed" value={data.managers.length.toLocaleString()} /><MetricCard label="League average" value={average.toFixed(1)} detail="GW points" /><MetricCard label="Elite average" value={elite.averageGw.toFixed(1)} detail={`${elite.elite.length} managers`} /><MetricCard label="Elite advantage" value={`${(elite.averageGw - average).toFixed(1)}`} detail="Points above league" tone="positive" /></section>
    <div className="elite-analysis-grid"><Chart title="Formation distribution" rows={formations} /><Chart title="GW points distribution" rows={points} /><Chart title="Captaincy concentration" rows={captains} /></div>
    <div className="content-grid elite-edge-grid"><Chart title="Squad value distribution" rows={costs} /><section className="surface"><div className="section-heading"><div><span>OWNERSHIP GAP</span><h2>Largest elite edges</h2><p>Percentage-point difference between top-5% and complete-league ownership.</p></div></div><div className="edge-list">{[...elite.ownership].sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge)).slice(0, 14).map((player) => <article key={player.element}><div><strong>{player.name}</strong><small>{player.position} · {player.team}</small></div><span>Elite {player.elitePercentage.toFixed(1)}%</span><span>League {player.leaguePercentage.toFixed(1)}%</span><b className={player.edge >= 0 ? "positive" : "negative"}>{player.edge >= 0 ? "+" : ""}{player.edge.toFixed(1)}pp</b></article>)}</div></section></div>
  </div>;
}
