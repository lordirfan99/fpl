import { ArrowRight, Clock3, ListChecks, ShieldCheck } from "lucide-react";
import { ChipNotice } from "@/components/chip-notice";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { getCompetitiveRecommendation } from "@/lib/competitive";
import { getDashboardData } from "@/lib/data";
import { getLiveTeam } from "@/lib/live";
import { deriveSeasonContext } from "@/lib/season";
import { formatMYT } from "@/lib/format";

const number = (value?: number) => typeof value === "number" ? value.toFixed(1) : "—";

const FRESHNESS_LABEL: Record<string, { label: string; detail: string; tone: "positive" | "warning" | "default" }> = {
  fresh: { label: "Fresh", detail: "Official live snapshot", tone: "positive" },
  provisional: { label: "League research", detail: "Current account not verified", tone: "warning" },
  stale: { label: "Stale", detail: "Older finalized snapshot", tone: "warning" },
  safe_hold: { label: "Safe hold", detail: "No fresh league data — see Telegram", tone: "warning" },
  needs_refresh: { label: "Needs refresh", detail: "No usable league snapshot", tone: "warning" },
};

export default async function AssistantPage() {
  const review = await getDashboardData().catch(() => null);
  // No explicit gw: let the API pick the freshest safe source (live snapshot
  // first, finalized fallback, honest safe_hold).
  const [rec, live] = await Promise.all([
    review ? getCompetitiveRecommendation(review.leagueId).catch(() => null) : null,
    review ? getLiveTeam(undefined, review.leagueId).catch(() => null) : null,
  ]);

  const season = review ? deriveSeasonContext(review.bootstrap.events, { finalizedGw: review.gameweek, liveGameweek: live?.gameweek }) : null;
  const targetGameweek = season?.nextDeadlineGw;
  // Trust the live chip only while its gameweek is the one being played and
  // has not been finalized yet — otherwise `active_chip` on a finished GW's
  // picks would report a spent chip forever.
  const liveChipNow = live && season && live.gameweek === season.liveGw && season.liveGw > season.finalizedGw ? live.active_chip : null;
  const deadline = season?.nextDeadline ? new Date(season.nextDeadline) : null;
  const hoursRemaining = season?.hoursToDeadline ?? null;

  const fresh = rec?.freshness;
  const packet = rec?.packetStatus ?? "advisory";
  const held = !rec || fresh?.stale !== false || packet === "safe_hold" || packet === "needs_refresh";
  const accountUnverified = fresh?.accountStateVerified !== true;
  const actionHeld = held || accountUnverified;
  const freshMeta = !held && accountUnverified ? FRESHNESS_LABEL.provisional : FRESHNESS_LABEL[fresh?.status ?? "stale"] ?? FRESHNESS_LABEL.stale;
  const ageLabel = fresh?.dataAgeHours == null ? "—"
    : fresh.dataAgeHours < 1 ? `${Math.round(fresh.dataAgeHours * 60)}m ago`
    : fresh.dataAgeHours < 48 ? `${fresh.dataAgeHours.toFixed(1)}h ago`
    : `${Math.floor(fresh.dataAgeHours / 24)}d ago`;

  const move = actionHeld ? undefined : rec?.transfers?.[0];
  const captain = actionHeld ? undefined : rec?.captains?.[0];
  const phase = rec?.competitive.phase;
  const alignment = rec?.competitive.alignment;
  const targetAlignment = rec?.competitive.targetAlignment;

  return <div className="page-stack">
    <PageHeader
      eyebrow={`DECISION ASSISTANT · TARGET GW${targetGameweek ?? "—"}`}
      title="Your next deadline, made clear"
      description="League research from public gameweek picks. Personal plans require a verified current squad and budget from your Telegram planner."
      updated={formatMYT(rec?.meta.snapshotAt)}
    />

    <ChipNotice targetGameweek={targetGameweek} liveGameweek={live?.gameweek} liveActiveChip={liveChipNow} />

    <section className="decision-hero">
      <div>
        <span className="hero-kicker">NEXT DEADLINE · GW{targetGameweek ?? "—"} · {phase ?? "REVIEW"}</span>
        <h2>{move ? <>{move.outgoing.name} <ArrowRight size={22} /> {move.incoming.name}</> : actionHeld ? "Personal recommendation pending" : "Set your lineup — no transfer"}</h2>
        <p>{actionHeld ? "Check the latest Telegram plan and its account-sync time before deciding. An empty transfer card does not mean you should hold your transfer." : rec?.competitive.phaseReason}</p>
      </div>
      <div className="hero-score">
        <span>{hoursRemaining == null ? "Deadline" : "Time left"}</span>
        <strong>{hoursRemaining == null ? "—" : hoursRemaining < 24 ? `${Math.ceil(hoursRemaining)}h` : hoursRemaining < 48 ? `${Math.floor(hoursRemaining / 24)}d ${Math.round(hoursRemaining % 24)}h` : `${Math.floor(hoursRemaining / 24)}d`}</strong>
      </div>
    </section>

    <section className="metric-grid">
      <MetricCard label="Action now" value={actionHeld ? "Check plan" : move ? "Transfer" : "Set XI"} detail={actionHeld ? "Current account verification required" : move ? "Model-supported candidate below" : "No move clears the model threshold"} tone={move ? "positive" : "default"} />
      <MetricCard label="Target gameweek" value={`GW${targetGameweek ?? "—"}`} detail={formatMYT(deadline) ?? "Deadline TBC"} />
      <MetricCard label={accountUnverified ? "Public squad alignment" : "Elite alignment"} value={alignment == null ? "—" : `${alignment.toFixed(0)}%`} detail={accountUnverified ? `Based on GW${fresh?.accountGameweek ?? rec?.gameweek ?? "—"} picks` : targetAlignment == null ? "Target pending" : `Target ${targetAlignment}%`} tone={alignment != null && targetAlignment != null && alignment >= targetAlignment ? "positive" : "warning"} />
      <MetricCard label="Recommendation" value={freshMeta.label} detail={`${freshMeta.detail} · ${ageLabel}`} tone={freshMeta.tone} />
    </section>

    {held ? <section className="execution-note"><ShieldCheck /><div><strong>Holding — fresh inputs unavailable</strong><p>Check the latest Telegram plan and its input status. No transfer or captain suggestion is available from this page until the required data is verified.</p></div></section> : null}
    {!held && accountUnverified ? <section className="execution-note"><Clock3 /><div><strong>Fresh league data · current account unverified</strong><p>The public squad and bank belong to GW{fresh?.accountGameweek ?? rec?.gameweek}. They may not include your transfers since that deadline. The VM planner checks your authenticated squad, bank, selling prices and free transfers before building a personal plan.</p></div></section> : null}

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
        </article> : <div className="empty-state"><ShieldCheck /><h3>{actionHeld ? "Personal plan needs verification" : "Keep your transfer"}</h3><p>{actionHeld ? "Review the latest verified Telegram plan. This public page does not know your current account squad or budget." : "No move clears the model threshold on the freshest available data. Focus on the XI and captain."}</p></div>}
      </section>
      <section className="surface">
        <div className="section-heading"><div><span>LINEUP CHECK</span><h2>Captain and formation</h2></div><span className="section-chip">Model recommendation</span></div>
        <div className="captain-list"><article className="captain-row recommended">
          <span>C</span>
          <div><strong>{captain?.name ?? "Captain pending"}</strong><small>{rec?.competitive.templateFormation ?? "Formation pending"} · GW{targetGameweek ?? "—"}</small></div>
          <b>{number(captain?.score)}<small>score</small></b>
          <em>{actionHeld ? "Current account picks must be verified first" : "Confirm final team news before you set your captain"}</em>
        </article></div>
      </section>
    </div>

    <section className="surface">
      <div className="section-heading"><div><span>WHY YOU CAN TRUST THIS</span><h2>Decision readiness</h2><p>Each status answers a different question, so &ldquo;valid&rdquo; is never confused with &ldquo;fresh&rdquo;.</p></div></div>
      <div className="validation-grid">
        <div className={fresh && !fresh.stale ? "passed" : "failed"}><ShieldCheck /><span>Data: {fresh?.source ?? "unknown"} · {freshMeta.label.toLowerCase()}{fresh?.snapshotAt ? ` · ${formatMYT(fresh.snapshotAt)}` : ""}</span></div>
        <div className={deadline && hoursRemaining != null && hoursRemaining > 0 ? "passed" : "failed"}><Clock3 /><span>Deadline: {formatMYT(deadline) ?? "unknown"}</span></div>
        <div className="passed"><ListChecks /><span>Execution: manual — you apply changes in the official FPL app</span></div>
      </div>
    </section>

    {review && targetGameweek != null && targetGameweek !== review.gameweek ? <section className="execution-note"><Clock3 /><div><strong>Historical research is deliberately separated</strong><p>The finalized team and league review is GW{review.gameweek}; the next deadline is GW{targetGameweek}. The dashboard will not use that older review to manufacture a current transfer recommendation.</p></div></section> : null}
  </div>;
}
