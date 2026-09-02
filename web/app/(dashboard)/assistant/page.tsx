import { ArrowRight, Clock3, ListChecks, ShieldCheck } from "lucide-react";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { getCompetitiveRecommendation } from "@/lib/competitive";
import { getDashboardData } from "@/lib/data";
import { deriveSeasonContext } from "@/lib/season";

const number = (value?: number) => typeof value === "number" ? value.toFixed(1) : "—";

export default async function AssistantPage() {
  const review = await getDashboardData().catch(() => null);
  const rec = review ? await getCompetitiveRecommendation(review.leagueId, review.gameweek).catch(() => null) : null;

  const season = review ? deriveSeasonContext(review.bootstrap.events, { finalizedGw: review.gameweek }) : null;
  const targetGameweek = season?.nextDeadlineGw;
  const deadline = season?.nextDeadline ? new Date(season.nextDeadline) : null;
  const hoursRemaining = season?.hoursToDeadline ?? null;

  const move = rec?.transfers?.[0];
  const captain = rec?.captains?.[0];
  const phase = rec?.competitive.phase;
  const alignment = rec?.competitive.alignment;
  const targetAlignment = rec?.competitive.targetAlignment;
  const action = move ? "TRANSFER" : "LINEUP ONLY";
  const quality = rec?.meta.qualityStatus ?? "unknown";

  return <div className="page-stack">
    <PageHeader
      eyebrow={`DECISION ASSISTANT · TARGET GW${targetGameweek ?? "—"}`}
      title="Your next deadline, made clear"
      description="One locally derived, read-only recommendation. Review it, then make every transfer, captain and lineup change yourself in the official FPL app."
      updated={rec?.meta.snapshotAt ? new Date(rec.meta.snapshotAt).toLocaleString("en-MY", { dateStyle: "medium", timeStyle: "short" }) : undefined}
    />

    <section className="decision-hero">
      <div>
        <span className="hero-kicker">NEXT DEADLINE · GW{targetGameweek ?? "—"} · {phase ?? "REVIEW"}</span>
        <h2>{move ? <>{move.outgoing.name} <ArrowRight size={22} /> {move.incoming.name}</> : "Set your lineup — no transfer"}</h2>
        <p>{rec?.competitive.phaseReason ?? "A finalized league snapshot for the current gameweek is not available yet, so no competitor-aware recommendation can be built."} Review and apply any change manually in FPL.</p>
      </div>
      <div className="hero-score">
        <span>{hoursRemaining == null ? "Deadline" : "Time left"}</span>
        <strong>{hoursRemaining == null ? "—" : hoursRemaining < 24 ? `${Math.ceil(hoursRemaining)}h` : `${Math.floor(hoursRemaining / 24)}d`}</strong>
      </div>
    </section>

    <section className="metric-grid">
      <MetricCard label="Action now" value={move ? "Transfer" : "Set XI"} detail={move ? "Model-supported candidate below" : "No move clears the model threshold"} tone={move ? "positive" : "default"} />
      <MetricCard label="Target gameweek" value={`GW${targetGameweek ?? "—"}`} detail={deadline ? deadline.toLocaleString("en-MY", { dateStyle: "medium", timeStyle: "short" }) : "Deadline TBC"} />
      <MetricCard label="Elite alignment" value={alignment == null ? "—" : `${alignment.toFixed(0)}%`} detail={targetAlignment == null ? "Target pending" : `Target ${targetAlignment}%`} tone={alignment != null && targetAlignment != null && alignment >= targetAlignment ? "positive" : "warning"} />
      <MetricCard label="Snapshot quality" value={quality === "valid" ? "Valid" : quality === "unknown" ? "Pending" : "Invalid"} detail={rec ? `Reviewed GW${review?.gameweek}` : "Awaiting snapshot"} tone={quality === "valid" ? "positive" : "warning"} />
    </section>

    <div className="content-grid decision-grid">
      <section className="surface">
        <div className="section-heading"><div><span>WHAT TO DO</span><h2>{move ? "Transfer candidate" : "Lineup action"}</h2></div><span className="section-chip">GW{targetGameweek ?? "—"}</span></div>
        {move ? <article className="action-row">
          <span className="action-state do">MOVE</span>
          <div>
            <strong>{move.outgoing.name} <ArrowRight size={14} /> {move.incoming.name}</strong>
            <small>{move.incoming.position} · {move.incoming.team} · {move.incoming.fixture}</small>
            <p>{number(move.xptsGain)} gross next-GW xPts · {move.incoming.eliteOwnership.toFixed(1)}% elite ownership · hits excluded</p>
          </div>
          <b>Apply in FPL<small>after team news</small></b>
        </article> : <div className="empty-state"><ShieldCheck /><h3>Keep your transfer</h3><p>No move clears the model threshold on the latest finalized snapshot. Focus on the XI and captain.</p></div>}
      </section>
      <section className="surface">
        <div className="section-heading"><div><span>LINEUP CHECK</span><h2>Captain and formation</h2></div><span className="section-chip">Model recommendation</span></div>
        <div className="captain-list"><article className="captain-row recommended">
          <span>C</span>
          <div><strong>{captain?.name ?? "Captain pending"}</strong><small>{rec?.competitive.templateFormation ?? "Formation pending"} · GW{targetGameweek ?? "—"}</small></div>
          <b>{number(captain?.score)}<small>score</small></b>
          <em>Confirm final team news before you set your captain</em>
        </article></div>
      </section>
    </div>

    <section className="surface">
      <div className="section-heading"><div><span>WHY YOU CAN TRUST THIS</span><h2>Decision readiness</h2><p>Each status answers a different question, so &ldquo;valid&rdquo; is never confused with &ldquo;fresh&rdquo;.</p></div></div>
      <div className="validation-grid">
        <div className={quality === "valid" ? "passed" : "failed"}><ShieldCheck /><span>Snapshot: {quality}</span></div>
        <div className={deadline && hoursRemaining != null && hoursRemaining > 0 ? "passed" : "failed"}><Clock3 /><span>Deadline: {deadline ? deadline.toLocaleString("en-MY", { dateStyle: "medium", timeStyle: "short" }) : "unknown"}</span></div>
        <div className="passed"><ListChecks /><span>Execution: manual — you apply changes in the official FPL app</span></div>
      </div>
    </section>

    {review && targetGameweek != null && targetGameweek !== review.gameweek ? <section className="execution-note"><Clock3 /><div><strong>Historical research is deliberately separated</strong><p>The finalized team and league review is GW{review.gameweek}; the next deadline is GW{targetGameweek}. The dashboard will not use that older review to manufacture a current transfer recommendation.</p></div></section> : null}
  </div>;
}
