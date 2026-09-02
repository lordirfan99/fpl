import { PageHeader } from "@/components/page-header";
import { getPlannerData } from "@/lib/data";
import { getCompetitiveRecommendation } from "@/lib/competitive";
import { TransferDraft } from "@/components/transfer-draft";
import type { Fixture, Pick } from "@/lib/types";

export default async function PlannerPage({ searchParams }: { searchParams: Promise<{ gw?: string }> }) {
  const query = await searchParams;
  const requested = Number(query.gw);
  const targetGameweek = Number.isInteger(requested) && requested >= 1 && requested <= 38 ? requested : undefined;
  const data = await getPlannerData(targetGameweek);
  const plan = targetGameweek === data.gameweek ? await getCompetitiveRecommendation(data.leagueId, data.gameweek).catch(() => null) : null;
  const gameweeks = Array.from({ length: data.toGameweek - data.fromGameweek + 1 }, (_, index) => data.fromGameweek + index);
  const playerIndex = new Map(data.bootstrap.elements.map((player) => [player.id, player]));
  const starters = data.manager.squad.slice(0, 11);
  const weekly = gameweeks.map((gameweek) => summarizeWeek(gameweek, starters, data.fixtureHorizon[String(gameweek)] ?? [], playerIndex));
  const draftPlayers = data.bootstrap.elements.map(({ id, web_name, element_type, now_cost, ep_next, team, status }) => ({ id, web_name, element_type, now_cost, ep_next, team, status }));
  const draftTeams = data.bootstrap.teams.map(({ id, short_name }) => ({ id, short_name }));
  return <div className="page-stack"><PageHeader eyebrow={`TRANSFER PLANNER · START GW${data.fromGameweek}`} title="Plan the next five gameweeks" description={`Official FPL fixtures and difficulty for GW${data.fromGameweek}–GW${data.toGameweek}. This begins at the same upcoming deadline as the decision board.`} />
    <section className="surface planner-note"><span>STARTING POINT</span><h2>{plan?.transfers.length ? "Model-supported transfer available" : "Hold the transfer for now"}</h2><p>{plan?.competitive.phaseReason ?? "This is fixture research only. Review every move and apply it yourself in the official FPL app."}</p></section>
    <section className="surface"><div className="section-heading"><div><span>FIXTURE HORIZON</span><h2>Five-week squad outlook</h2></div><span className="section-chip">Official FPL FDR</span></div><div className="horizon-cards">{weekly.map((week, index) => <article className={index === 0 ? "active" : ""} key={week.gameweek}><div><span>GW{week.gameweek}</span><b className={`fdr-${Math.round(week.averageFdr)}`}>{week.averageFdr.toFixed(1)} avg FDR</b></div><strong>{week.easy} favourable · {week.hard} difficult</strong><small>Captain fixture: {week.captain}</small></article>)}</div></section>
    <section className="surface fixture-matrix-surface"><div className="section-heading"><div><span>YOUR SQUAD</span><h2>Player-by-player fixture run</h2></div><span className="section-chip">11 starters + 4 bench</span></div><div className="fixture-matrix-wrap"><table className="fixture-matrix"><thead><tr><th>Player</th>{gameweeks.map((gameweek) => <th key={gameweek}>GW{gameweek}</th>)}</tr></thead><tbody>{data.manager.squad.map((pick, index) => <tr className={index === 11 ? "bench-start" : ""} key={pick.element}><td><strong>{pick.name}</strong><small>{index < 11 ? "XI" : "Bench"} · {pick.position}</small></td>{gameweeks.map((gameweek) => <td key={gameweek}><FixtureCell pick={pick} fixtures={data.fixtureHorizon[String(gameweek)] ?? []} /></td>)}</tr>)}</tbody></table></div></section>
    <TransferDraft squad={data.manager.squad} players={draftPlayers} teams={draftTeams} gameweek={data.fromGameweek} />
    <section className="surface"><div className="section-heading"><div><span>GW{data.fromGameweek} SHORTLIST</span><h2>Current transfer candidates</h2></div><span className="section-chip">{plan ? "Elite + model" : "Awaiting aligned league snapshot"}</span></div>{plan?.transfers.length ? <div className="planner-grid">{plan.transfers.map((move) => <article key={`${move.outgoing.element}-${move.incoming.element}`}><span>{move.outgoing.name}</span><strong>→ {move.incoming.name}</strong><small>{move.incoming.position} · {move.incoming.team} · {move.incoming.fixture}</small><div><b>{move.xptsGain >= 0 ? "+" : ""}{move.xptsGain.toFixed(1)} gross next-GW xPts</b><em>{move.incoming.eliteOwnership.toFixed(1)}% elite · hits excluded</em></div></article>)}</div> : <div className="empty-state"><h3>Research only until snapshots align</h3><p>League and team review data currently ends at GW{data.gameweek}; no historical shortlist is presented as a GW{data.fromGameweek} recommendation.</p></div>}</section>
    <section className="surface planner-note"><span>HOW TO USE THIS</span><h2>Look beyond one gameweek</h2><p>Prioritise players with several green fixtures, not a single easy match. FDR is schedule context—not a points guarantee—so confirm injuries, minutes and late team news before you make a move in the official FPL app.</p></section>
  </div>;
}

function fixturesFor(team: string, fixtures: Fixture[]) {
  return fixtures.filter((fixture) => fixture.team_h === team || fixture.team_a === team).map((fixture) => fixture.team_h === team
    ? { label: `${shortTeam(fixture.team_a)} (H)`, fdr: fixture.team_h_difficulty }
    : { label: `${shortTeam(fixture.team_h)} (A)`, fdr: fixture.team_a_difficulty });
}

function FixtureCell({ pick, fixtures }: { pick: Pick; fixtures: Fixture[] }) {
  const matches = fixturesFor(pick.team, fixtures);
  if (!matches.length) return <span className="fixture-chip blank">TBC</span>;
  return <div className="fixture-cell">{matches.map((match, index) => <span className={`fixture-chip fdr-${match.fdr}`} key={`${match.label}-${index}`}>{match.label}<b>{match.fdr}</b></span>)}</div>;
}

function summarizeWeek(gameweek: number, starters: Pick[], fixtures: Fixture[], playerIndex: Map<number, { form: string }>) {
  const schedule = starters.flatMap((pick) => fixturesFor(pick.team, fixtures).map((match) => ({ ...match, pick })));
  const averageFdr = schedule.reduce((sum, item) => sum + item.fdr, 0) / Math.max(1, schedule.length);
  const captain = [...schedule].sort((a, b) => (a.fdr - b.fdr) || (Number(playerIndex.get(b.pick.element)?.form ?? 0) - Number(playerIndex.get(a.pick.element)?.form ?? 0)))[0];
  return { gameweek, averageFdr, easy: schedule.filter((item) => item.fdr <= 2).length, hard: schedule.filter((item) => item.fdr >= 4).length, captain: captain ? `${captain.pick.name} · ${captain.label}` : "TBC" };
}

function shortTeam(team: string) {
  const aliases: Record<string, string> = { "Manchester City": "MCI", "Manchester United": "MUN", "Nott'm Forest": "NFO", "Crystal Palace": "CRY", "Newcastle United": "NEW", "Ipswich Town": "IPS", "Coventry City": "COV", "Hull City": "HUL", "Aston Villa": "AVL", "Wolverhampton Wanderers": "WOL" };
  return aliases[team] ?? team.slice(0, 3).toUpperCase();
}
