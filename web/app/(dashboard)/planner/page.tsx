import Link from "next/link";
import "@/app/decision-room.css";
import { PageHeader } from "@/components/page-header";
import { DecisionRoom } from "@/components/decision-room";
import { getPlannerData } from "@/lib/data";
import { getPrivateDashboard } from "@/lib/private-dashboard";
import { TransferDraft } from "@/components/transfer-draft";
import type { Fixture, Pick } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PlannerPage({ searchParams }: { searchParams: Promise<{ gw?: string }> }) {
  const query = await searchParams;
  const requested = Number(query.gw);
  const targetGameweek = Number.isInteger(requested) && requested >= 1 && requested <= 38 ? requested : undefined;
  const privatePlan = targetGameweek ? null : await getPrivateDashboard();
  if (privatePlan?.status === "ready" && privatePlan.packet) {
    return <div className="page-stack decision-home"><PageHeader eyebrow="VERIFIED PERSONAL PLAN" title="Your current decision plan" description="The same plan identifier, squad changes and captain order published by the VM for Telegram." />
      <DecisionRoom packet={privatePlan.packet} checkedAt={privatePlan.account_checked_at} />
    </div>;
  }
  const data = await getPlannerData(targetGameweek);
  const gameweeks = Array.from({ length: data.toGameweek - data.fromGameweek + 1 }, (_, index) => data.fromGameweek + index);
  const starters = data.manager.squad.slice(0, 11);
  const weekly = gameweeks.map((gameweek) => summarizeWeek(gameweek, starters, data.fixtureHorizon[String(gameweek)] ?? []));
  const draftPlayers = data.bootstrap.elements.map(({ id, web_name, element_type, now_cost, ep_next, team, status }) => ({ id, web_name, element_type, now_cost, ep_next, team, status }));
  const draftTeams = data.bootstrap.teams.map(({ id, short_name }) => ({ id, short_name }));
  return <div className="page-stack"><PageHeader eyebrow={`FIXTURE RESEARCH · START GW${data.fromGameweek}`} title="Explore the next five gameweeks" description={`Official FPL fixtures and difficulty for GW${data.fromGameweek}–GW${data.toGameweek}. This page is research, not a verified personal recommendation.`} />
    <section className="surface planner-note"><span>{targetGameweek ? "HISTORICAL / FUTURE RESEARCH" : "PERSONAL PLAN UNAVAILABLE"}</span><h2>No transfer or hold recommendation is shown</h2><p>{targetGameweek ? `You selected GW${targetGameweek}. Use these recorded fixtures for research only.` : "Sign in with the owner Google account and wait for a current verified VM packet to use the decision plan."}</p><p><Link href={privatePlan?.status === "signed_out" ? "/sign-in" : "/this-week"}>{privatePlan?.status === "signed_out" ? "Sign in with Google" : "Check plan status"}</Link></p></section>
    <section className="surface"><div className="section-heading"><div><span>FIXTURE HORIZON</span><h2>Five-week squad outlook</h2></div><span className="section-chip">Official FPL FDR</span></div><div className="horizon-cards">{weekly.map((week, index) => <article className={index === 0 ? "active" : ""} key={week.gameweek}><div><span>GW{week.gameweek}</span><b className={`fdr-${Math.round(week.averageFdr)}`}>{week.averageFdr.toFixed(1)} avg FDR</b></div><strong>{week.easy} favourable · {week.hard} difficult</strong><small>Easiest recorded fixture: {week.easiest}</small></article>)}</div></section>
    <section className="surface fixture-matrix-surface"><div className="section-heading"><div><span>YOUR SQUAD</span><h2>Player-by-player fixture run</h2></div><span className="section-chip">11 starters + 4 bench</span></div><div className="fixture-matrix-wrap"><table className="fixture-matrix"><thead><tr><th>Player</th>{gameweeks.map((gameweek) => <th key={gameweek}>GW{gameweek}</th>)}</tr></thead><tbody>{data.manager.squad.map((pick, index) => <tr className={index === 11 ? "bench-start" : ""} key={pick.element}><td><strong>{pick.name}</strong><small>{index < 11 ? "XI" : "Bench"} · {pick.position}</small></td>{gameweeks.map((gameweek) => <td key={gameweek}><FixtureCell pick={pick} fixtures={data.fixtureHorizon[String(gameweek)] ?? []} /></td>)}</tr>)}</tbody></table></div></section>
    {targetGameweek ? <TransferDraft squad={data.manager.squad} players={draftPlayers} teams={draftTeams} gameweek={data.fromGameweek} /> : null}
    <section className="surface"><div className="section-heading"><div><span>DECISION BOUNDARY</span><h2>Research does not become advice</h2></div><span className="section-chip">No personal account state</span></div><div className="empty-state"><h3>Use the verified plan for choices</h3><p>This public view does not know your current selling prices, free transfers, live chip state or post-deadline account changes. It therefore cannot advise a transfer, captain or hold.</p></div></section>
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

function summarizeWeek(gameweek: number, starters: Pick[], fixtures: Fixture[]) {
  const schedule = starters.flatMap((pick) => fixturesFor(pick.team, fixtures).map((match) => ({ ...match, pick })));
  const averageFdr = schedule.reduce((sum, item) => sum + item.fdr, 0) / Math.max(1, schedule.length);
  const easiest = [...schedule].sort((a, b) => a.fdr - b.fdr || a.pick.name.localeCompare(b.pick.name))[0];
  return { gameweek, averageFdr, easy: schedule.filter((item) => item.fdr <= 2).length, hard: schedule.filter((item) => item.fdr >= 4).length, easiest: easiest ? `${easiest.pick.name} · ${easiest.label}` : "Unavailable" };
}

function shortTeam(team: string) {
  const aliases: Record<string, string> = { "Manchester City": "MCI", "Manchester United": "MUN", "Nott'm Forest": "NFO", "Crystal Palace": "CRY", "Newcastle United": "NEW", "Ipswich Town": "IPS", "Coventry City": "COV", "Hull City": "HUL", "Aston Villa": "AVL", "Wolverhampton Wanderers": "WOL" };
  return aliases[team] ?? team.slice(0, 3).toUpperCase();
}
