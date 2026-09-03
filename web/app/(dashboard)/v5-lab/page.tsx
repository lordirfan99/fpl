import { Activity, Beaker, BrainCircuit, CheckCircle2, FlaskConical, LockKeyhole, ShieldCheck } from "lucide-react";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { V5PlayerExplorer } from "@/components/v5-player-explorer";
import { LiveVsV5 } from "@/components/live-vs-v5";
import { getDashboardData } from "@/lib/data";
import { getLiveTeam } from "@/lib/live";
import { deriveSeasonContext } from "@/lib/season";
import { getV5Projections } from "@/lib/v5";
import { formatMYT } from "@/lib/format";

export default async function ModelsPage() {
  const [data, live, v5] = await Promise.all([getDashboardData().catch(() => null), getLiveTeam(), getV5Projections()]);
  const season = data ? deriveSeasonContext(data.bootstrap.events, { finalizedGw: data.gameweek, liveGameweek: live?.gameweek }) : null;
  const top = v5.players[0];
  const ready = v5.players.filter((player) => player.quality_issues.length === 0).length;
  return <div className="page-stack v5-page">
    <PageHeader eyebrow="MODELS · RESEARCH LANE" title="Models" description="Independent V5 projections and a read-only comparison of your live XI with the model's pick — kept outside the decision path." updated={formatMYT(v5.generated_at)} />
    <section className="v5-hero">
      <div><span><FlaskConical size={15} /> ISOLATED LABORATORY</span><h2>Predict football first.<br />Fight the league second.</h2><p>V5 separates raw player ability and fixture outcomes from ownership, rank and captaincy pressure. Its low/high ranges are heuristic, not calibrated quantiles. It cannot replace production or execute an FPL action.</p></div>
      <div className={`v5-live-orb ${v5.available ? "online" : "offline"}`}><Activity size={25} /><strong>{v5.available ? "LIVE" : "LOCAL"}</strong><small>{v5.projection_version}</small></div>
    </section>
    <div className="model-lanes">
      <article><ShieldCheck /><span>PRODUCTION</span><strong>competitive-v4.0</strong><small>Current decision reference</small></article>
      <article><LockKeyhole /><span>RETIRED</span><strong>V4.2 shadow</strong><small>External generator decommissioned</small></article>
      <article className="lab"><Beaker /><span>RESEARCH</span><strong>projection-v5.0-lab</strong><small>No execution authority</small></article>
    </div>
    {v5.available ? <>
      <div className="metric-grid"><MetricCard label="Target gameweek" value={`GW${v5.gameweek}`} detail="Next deadline-safe projection" /><MetricCard label="Player universe" value={String(v5.players.length)} detail="Not restricted by ownership" tone="positive" /><MetricCard label="Quality-ready" value={`${ready}/${v5.players.length}`} detail="Complete current inputs" /><MetricCard label="Top projection" value={top ? `${top.xpts_mean.toFixed(1)} xPts` : "—"} detail={top?.name ?? "No player rows"} /></div>
      <V5PlayerExplorer players={v5.players} />
    </> : <section className="surface v5-unavailable"><BrainCircuit /><div><span>DEPLOYMENT REQUIRED</span><h2>V5 API is not live yet</h2><p>The dashboard is ready, but the public API has not deployed <code>/v1/projections/current</code>. Production remains safe and unchanged.</p><small>{v5.reason}</small></div></section>}
    {data ? <LiveVsV5 live={live} v5={v5} bootstrap={data.bootstrap} nextDeadlineGw={season?.nextDeadlineGw ?? v5.gameweek} /> : null}
    <section className="shadow-safety"><div><CheckCircle2 /><div><strong>Ownership-independent</strong><small>Raw xPts never reads elite ownership or league rank.</small></div></div><div><LockKeyhole /><div><strong>Read-only by design</strong><small>No browser or API endpoint can mutate your FPL team.</small></div></div><div><ShieldCheck /><div><strong>Research only</strong><small>V5 stays a research lane; it is never an instruction to act.</small></div></div></section>
  </div>;
}
