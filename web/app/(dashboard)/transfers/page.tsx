import { LeagueSwitcher, resolveLeague } from "@/components/league-switcher";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { getLeagueData, getTransferOptimizer } from "@/lib/data";
import { analyzeElite, chipLabel } from "@/lib/elite";

type Count = { name: string; count: number; percentage: number };

function countRows(values: string[], denominator: number): Count[] {
  const counts = new Map<string, number>();
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return [...counts].map(([name, count]) => ({ name, count, percentage: count / Math.max(1, denominator) * 100 })).sort((a, b) => b.count - a.count);
}

function ActivityList({ rows, empty }: { rows: Count[]; empty: string }) {
  return rows.length ? <div className="ranked-list">{rows.slice(0, 15).map((row, index) => <article key={row.name}><span>{index + 1}</span><strong>{row.name}</strong><b>{row.count}<small>{row.percentage.toFixed(1)}%</small></b></article>)}</div> : <div className="empty-state"><h3>No activity recorded</h3><p>{empty}</p></div>;
}

export default async function TransfersPage({ searchParams }: { searchParams: Promise<{ league?: string; gw?: string }> }) {
  const params = await searchParams;
  const selected = resolveLeague(params.league), requestedGw = params.gw ? Math.max(1, Math.min(38, Number(params.gw) || 1)) : undefined, data = await getLeagueData(selected.id, requestedGw), elite = analyzeElite(data.managers);
  const moves = countRows(data.managers.flatMap((manager) => manager.transfer_details?.map((move) => `${move.out} → ${move.in}`) ?? []), data.managers.length);
  const chips = countRows(data.managers.flatMap((manager) => manager.chips_used?.map((chip) => chipLabel(chip.name ?? chip.chip_name ?? "Unknown")) ?? []), data.managers.length);
  const activeManagers = data.managers.filter((manager) => manager.transfers_made > 0).length;
  const eliteActive = elite.elite.filter((manager) => manager.transfers_made > 0).length;
  // The optimizer is personalized to the configured team. Public leagues can
  // still provide complete market intelligence when that team is not a member.
  const optimizer = data.manager ? await getTransferOptimizer(selected.id, data.gameweek).catch(() => null) : null;
  return <div className="page-stack"><PageHeader eyebrow={`MARKET INTELLIGENCE · GW${data.gameweek} ${data.liveProvisional ? "LIVE" : "SNAPSHOT"}`} title="Transfers & chips" description={data.liveProvisional ? "Live official FPL standings; transfer and points totals may still change before finalization." : "Track what the complete league and its top-5% cohort actually did in the selected gameweek."} updated={data.fetchedAt ? new Date(data.fetchedAt).toLocaleString("en-MY", { dateStyle: "medium", timeStyle: "short" }) : undefined} /><LeagueSwitcher selected={selected.id} pathname="/transfers" />
    <section className="metric-grid"><MetricCard label="League transfers" value={activeManagers.toLocaleString()} detail={`${(activeManagers / data.managers.length * 100).toFixed(1)}% active managers`} /><MetricCard label="Elite transfers" value={eliteActive.toLocaleString()} detail={`${(eliteActive / elite.elite.length * 100).toFixed(1)}% of top 5%`} /><MetricCard label="Top elite move" value={elite.transfers[0]?.name ?? "Hold"} detail={elite.transfers[0] ? `${elite.transfers[0].count} managers` : "No move recorded"} /><MetricCard label="Most-used chip" value={chips[0]?.name ?? "None"} detail={chips[0] ? `${chips[0].count} managers` : "No chip activity"} /></section>
    {optimizer ? <section className="surface table-surface"><div className="section-heading"><div><span>NET-EV RESEARCH · {optimizer.optimizer_version}</span><h2>League market data is ready</h2><p>Multi-week legal transfer plans include hit cost, budget, club limits, uncertainty penalty and the future value of a saved free transfer. Read-only.</p></div><span className="section-chip">GW{optimizer.target_gameweeks.join("–GW")}</span></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Plan</th><th>Moves</th><th>Gross horizon</th><th>Hits</th><th>FT value</th><th>NET EV</th><th>Bank</th></tr></thead><tbody>{optimizer.plans.slice(0, 6).map((plan, index) => <tr key={`${plan.transfer_count}-${index}`}><td><strong>{plan.transfer_count ? `Option ${index + 1}` : "Hold"}</strong></td><td>{plan.transfers?.map((move) => `${move.out_name} → ${move.in_name}`).join("; ") || "Roll transfer"}</td><td>{(plan.gross_horizon_gain ?? 0).toFixed(2)}</td><td>-{plan.hit_cost.toFixed(1)}</td><td>-{(plan.free_transfer_opportunity_cost ?? 0).toFixed(1)}</td><td><strong>{(plan.net_ev ?? 0).toFixed(2)}</strong></td><td>£{plan.bank_after.toFixed(1)}m</td></tr>)}</tbody></table></div><p className="research-note">{optimizer.disclaimer}</p></section> : <section className="surface optimizer-unavailable"><div><span>PERSONAL OPTIMIZER</span><h2>League market data is ready</h2><p>Your FPL team is not a member of {selected.name}, so a personalized transfer plan cannot be calculated for this league. The real league transfers, elite moves, and chip activity below remain available.</p></div><a href="/transfers?league=58005">Open your league optimizer</a></section>}
    <div className="content-grid elite-edge-grid"><section className="surface"><div className="section-heading"><div><span>ELITE CONSENSUS</span><h2>Top-5% transfer combinations</h2><p>Moves made by the strongest overall-ranked managers in this league.</p></div><span className="section-chip">{elite.elite.length} managers</span></div><ActivityList rows={elite.transfers} empty="The elite cohort recorded no transfer pairs for this snapshot." /></section><section className="surface"><div className="section-heading"><div><span>WHOLE LEAGUE</span><h2>Popular transfer combinations</h2><p>Use this as herd context, not an automatic recommendation.</p></div><span className="section-chip">{data.managers.length} managers</span></div><ActivityList rows={moves} empty="No league transfer pairs were recorded for this gameweek." /></section></div>
    <div className="content-grid elite-edge-grid"><section className="surface"><div className="section-heading"><div><span>CHIP TRACKER</span><h2>League chip usage</h2></div></div><ActivityList rows={chips} empty="No chips were recorded in this snapshot." /></section><section className="surface"><div className="section-heading"><div><span>ELITE CHIP TIMING</span><h2>Top-5% chip usage</h2></div></div><ActivityList rows={elite.chips} empty="No elite chip usage was recorded." /></section></div>
  </div>;
}
