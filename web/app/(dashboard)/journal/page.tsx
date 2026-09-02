import { Archive, BookOpenText, BrainCircuit, Download, ShieldCheck, Sparkles } from "lucide-react";
import { JournalTimeline } from "@/components/journal-timeline";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { buildSeasonTimeline, getJournalIndex, JOURNAL_EXPORT_URL } from "@/lib/journal";
import { getDashboardData } from "@/lib/data";

export default async function JournalPage() {
  const [journal, dashboard] = await Promise.all([getJournalIndex(), getDashboardData().catch(() => null)]);
  const timeline = buildSeasonTimeline(journal, dashboard?.bootstrap.events ?? []);
  const recorded = timeline.filter((row) => row.record_hash); const latest = recorded.at(-1); const valid = recorded.filter((row) => row.quality.status === "valid").length;
  return <div className="page-stack journal-page">
    <PageHeader eyebrow="SEASON MEMORY · 2026/27" title="Decision Journal" description="A permanent timeline of what we knew, what we chose, what happened and what the models learned." updated={journal.updated_at ? new Date(journal.updated_at).toLocaleString("en-MY", { timeZone: "Asia/Kuala_Lumpur" }) : undefined} />
    <section className="journal-hero"><div><span><BookOpenText size={15} /> LIVING SEASON ARCHIVE</span><h2>Turn every gameweek<br />into next season’s edge.</h2><p>Deadline evidence, final outcomes, league behaviour and model accuracy are connected in one research-ready record. The calendar stays visible all season, even before each week is archived.</p><a href={JOURNAL_EXPORT_URL}><Download size={15} /> Download season CSV</a></div><div className="journal-season-progress"><strong>{recorded.length}<small>/38</small></strong><span>Gameweeks archived</span><i style={{ "--journal-progress": `${recorded.length / 38 * 100}%` } as React.CSSProperties} /></div></section>
    <div className="metric-grid"><MetricCard label="Latest score" value={latest ? `${latest.summary.gw_points} pts` : "—"} detail={latest ? `GW${latest.gameweek}` : "Waiting for GW"} /><MetricCard label="Season total" value={String(journal.totals.points)} detail="Official FPL points" tone="positive" /><MetricCard label="Research ready" value={`${valid}/${journal.totals.completed}`} detail="Complete deadline evidence" tone={valid === journal.totals.completed ? "positive" : "warning"} /><MetricCard label="Latest league rank" value={latest ? `#${latest.summary.league_rank.toLocaleString()}` : "—"} detail="League 58005" /></div>
    <JournalTimeline season={journal.season} rows={timeline} />
    <section className="journal-principles"><article><Archive /><div><strong>Immutable evidence</strong><small>Deadline inputs are checksummed before outcomes exist.</small></div></article><article><BrainCircuit /><div><strong>Model accountability</strong><small>FPL, V4, V4.2 and V5 are compared with actual returns.</small></div></article><article><ShieldCheck /><div><strong>Private reflection</strong><small>Private notes stay private unless you add a public lesson.</small></div></article><article><Sparkles /><div><strong>Reusable research</strong><small>CSV, JSON and manifests survive beyond this dashboard.</small></div></article></section>
  </div>;
}
