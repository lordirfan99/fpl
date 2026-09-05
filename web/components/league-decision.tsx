import type { LeagueDecisionContext } from "@/lib/league-decision";
import { ComparisonBars } from "./decision-room";
import { displayNumber, numeric } from "@/lib/decision-room";
import { formatMYT } from "@/lib/format";

export function GoalProgress({ context, name }: { context: LeagueDecisionContext | null; name: string }) {
  const goal = context?.goal;
  const complete = goal?.available && numeric(goal.points_gap) && numeric(goal.cutoff_points) && numeric(goal.owner_points)
    && Number.isInteger(goal.owner_rank) && Number.isInteger(goal.cutoff_rank) && Number.isInteger(goal.manager_count)
    && typeof goal.inside_target === "boolean";
  if (!complete) return <section className="surface goal-progress"><span className="evidence-label">TOP 10% · {name}</span><h2>Target progress unavailable</h2><p>Complete standings including your entry are required. We won’t calculate a cutoff from a partial sample.</p></section>;
  const gap = goal.points_gap!;
  return <section className="surface goal-progress"><div className="goal-heading"><div><span className="evidence-label">RECORDED FACT · {name}</span>
    <h2>{gap > 0 ? `${gap} points to the top-10% cutoff` : gap < 0 ? `${-gap} points ahead of the cutoff` : "Level on points with the cutoff"}</h2>
    <p>Rank {goal.owner_rank} / {goal.manager_count?.toLocaleString()} · Target rank {goal.cutoff_rank} or better</p></div><span className={`goal-status ${goal.inside_target ? "inside" : ""}`}>{goal.inside_target ? "Inside top 10%" : "Chasing top 10%"}</span></div>
    <ComparisonBars rows={[{ label: "Your total", value: goal.owner_points ?? null, proposed: true }, { label: "Top-10% cutoff", value: goal.cutoff_points ?? null }]} unit="points" />
    {goal.tied_cutoff ? <p>Tied on points does not guarantee a place inside the target. Your official league rank determines the status above.</p> : null}
    <p className="decision-caption">Captured {formatMYT(context?.snapshot_at) ?? "time unavailable"} · GW{context?.gameweek}. {context?.status === "historical" ? "Older capture: not current standings." : "Live/provisional standings may change."} The cutoff moves as your rivals score.</p>
    <details className="player-evidence"><summary>Are you closing the gap?</summary>
      {context?.history?.length ? <><ComparisonBars rows={context.history.map(h => ({ label: `GW${h.gameweek}`, value: h.points_gap }))} unit="points behind cutoff" /><p>Negative means ahead. Missing archives stay unavailable. Each week uses that week’s membership and cutoff; this is recorded history, not a forecast.</p></> : <p>No comparable archived standings yet.</p>}
    </details>
  </section>;
}

export function RivalExposure({ context, ownedIds }: { context: LeagueDecisionContext | null; ownedIds?: number[] }) {
  const sample = context?.ownership;
  const covered = sample && Number.isInteger(sample.sample_count) && Number.isInteger(sample.population)
    && Number.isInteger(sample.cohort_count) && Number.isInteger(sample.cohort_sample)
    && Number.isInteger(sample.cohort_rank_threshold);
  if (!sample?.rows.length || !covered) return <section className="surface"><h2>Rival evidence unavailable</h2><p>A complete league capture and identified top-10% cohort are required.</p></section>;
  const own = ownedIds ? new Set(ownedIds) : null;
  const rows = [...sample.rows].sort((a, b) => {
    const aOwned = own ? own.has(a.element) : a.owned_at_snapshot;
    const bOwned = own ? own.has(b.element) : b.owned_at_snapshot;
    return Number(aOwned ?? true) - Number(bOwned ?? true) || (b.target_pct ?? -1) - (a.target_pct ?? -1);
  }).slice(0, 8);
  return <section className="surface rival-exposure"><span className="evidence-label">RECORDED FACT · GW{context?.gameweek} PICKS</span><h2>Where your rivals can hurt—or help—you</h2>
    <p>League: {sample.sample_count}/{sample.population} squads · Target group: {sample.cohort_sample}/{sample.cohort_count} squads at official rank {sample.cohort_rank_threshold} or better.</p>
    <p className="decision-caption">{own ? "Your side uses the verified current squad." : `Your side also uses public GW${context?.gameweek} picks.`} Rivals’ transfers after that deadline are not visible. Ownership is not a buy recommendation.{context?.status === "historical" ? " This capture is old." : ""}</p>
    <div className="exposure-grid">{rows.map(p => { const owned = own ? own.has(p.element) : p.owned_at_snapshot; return <article key={p.element}>
      <div className="exposure-heading"><strong>{p.name}</strong><span>{owned === null ? "Your ownership unknown" : owned ? "You own" : "Not in your squad"}</span></div>
      <ComparisonBars rows={[{ label: "Top-10% group", value: p.target_pct, proposed: true }, { label: "Whole league", value: p.league_pct }]} unit="% owned" scaleMax={100} />
      <p>{numeric(p.target_captain_pct) ? `${displayNumber(p.target_captain_pct)}% of sampled target managers captained this player in GW${context?.gameweek}.` : `Target-group captaincy is unavailable for GW${context?.gameweek}.`}</p>
      <small>{owned === null ? "Compare with your verified squad before drawing a conclusion." : owned ? "Shared points offer less separation from managers who also own this player." : "A return helps those owners relative to you; that exposure alone does not justify a transfer."}</small>
    </article>; })}</div>
    <p className="decision-caption">Ownership is unweighted squad ownership—not effective ownership or a predicted rank swing. Includes bench players.</p>
  </section>;
}
