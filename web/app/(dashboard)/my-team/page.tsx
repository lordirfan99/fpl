import { ChipNotice } from "@/components/chip-notice";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { Pitch } from "@/components/pitch";
import { getCompetitiveRecommendation } from "@/lib/competitive";
import { getDashboardData } from "@/lib/data";
import { getLiveTeam } from "@/lib/live";
import { deriveSeasonContext } from "@/lib/season";
import type { Pick } from "@/lib/types";
import { formatMYT } from "@/lib/format";

const positionFromElementType = (elementType?: number): Pick["position"] => elementType === 1 ? "GKP" : elementType === 2 ? "DEF" : elementType === 3 ? "MID" : "FWD";

export default async function MyTeamPage() {
  const [data, live] = await Promise.all([getDashboardData(), getLiveTeam()]);
  const rec = await getCompetitiveRecommendation(data.leagueId, data.gameweek).catch(() => null);
  const { manager } = data;

  const season = deriveSeasonContext(data.bootstrap.events, { finalizedGw: data.gameweek, liveGameweek: live?.gameweek });
  const targetGameweek = season.nextDeadlineGw;
  const liveGameweek = live?.gameweek;
  const liveTeam = liveGameweek != null && liveGameweek >= data.gameweek && live?.picks?.length === 15;
  const displayGameweek = liveTeam ? liveGameweek : data.gameweek;
  const livePlayers = live?.picks?.length === 15 ? live.picks.map((pick) => ({ element: pick.element, name: pick.web_name, position: positionFromElementType(data.bootstrap.elements.find((player) => player.id === pick.element)?.element_type), team: data.bootstrap.teams.find((team) => team.id === pick.team)?.name ?? "—", cost: pick.now_cost / 10, multiplier: pick.multiplier, is_captain: pick.is_captain, is_vice_captain: pick.is_vice_captain })) : [];
  const squad: Pick[] = liveTeam ? livePlayers : manager.squad;
  const historical = !liveTeam && targetGameweek !== data.gameweek;
  const deadline = season.nextDeadline ? new Date(season.nextDeadline) : null;
  const livePoints = live?.points;
  const liveTotal = live?.entry.total_points;
  const liveOverallRank = live?.entry.overall_rank;
  const liveLeagueRank = live?.league?.entry_rank;

  const move = rec?.transfers?.[0];
  const flagged = rec?.risks?.map((player) => player.name).filter(Boolean) ?? [];

  const playerMeta = (pick: Pick) => {
    const player = data.bootstrap.elements.find((row) => row.id === pick.element);
    const teamId = player?.team;
    const relevantFixtures = live?.fixtures.filter((fixture) => fixture.team_h === teamId || fixture.team_a === teamId) ?? [];
    if (relevantFixtures.some((fixture) => fixture.started)) return `${live?.picks.find((row) => row.element === pick.element)?.points ?? 0} pts`;
    const nextFixture = relevantFixtures[0];
    if (!nextFixture || !teamId) return "Fixture TBC";
    const opponentId = nextFixture.team_h === teamId ? nextFixture.team_a : nextFixture.team_h;
    const opponent = data.bootstrap.teams.find((team) => team.id === opponentId)?.short_name ?? "TBC";
    return `${opponent} (${nextFixture.team_h === teamId ? "H" : "A"})`;
  };

  return <div className="page-stack">
    <PageHeader eyebrow={`${liveTeam ? "OFFICIAL LIVE TEAM" : historical ? "LAST TEAM CAPTURE" : "MY TEAM"} · GW${displayGameweek}`} title={live?.entry.entry_name || manager.entry_name} description={`${live?.entry.player_name || manager.player_name} · FPL ID ${manager.entry_id.toLocaleString()}${liveTeam ? ` · Current GW${displayGameweek} squad, score and ranks from official FPL.` : historical ? ` · This is a completed-GW review, not the current GW${targetGameweek} lineup.` : ""}`} updated={formatMYT(liveTeam && live?.fetched_at ? live.fetched_at : data.fetchedAt)} />
    {liveTeam ? <section className="execution-note"><span className="status-dot" /><div><strong>GW{displayGameweek} official live data</strong><p>The squad, provisional score, total points and ranks below come directly from FPL. They refresh when this page loads and can still change until the Gameweek is finalised.</p></div></section> : null}
    {historical ? <section className="execution-note"><span className="status-dot warning" /><div><strong>Historical squad view</strong><p>This pitch is the latest captured GW{data.gameweek} team. For the upcoming GW{targetGameweek} deadline, use the Assistant recommendation and apply any change yourself in the official FPL app.</p></div></section> : null}
    <ChipNotice targetGameweek={targetGameweek} liveActiveChip={live && live.gameweek === targetGameweek ? live.active_chip : null} />
    <section className="metric-grid"><MetricCard label={`GW${displayGameweek} points`} value={liveTeam && livePoints != null ? `${livePoints}` : `${manager.gw_points}`} detail={liveTeam ? "Official live · provisional" : "Completed snapshot"} tone={liveTeam ? "warning" : undefined} /><MetricCard label="Overall points" value={`${liveTeam && liveTotal != null ? liveTotal : manager.total_points}`} detail={`Overall rank #${(liveTeam && liveOverallRank ? liveOverallRank : manager.overall_rank).toLocaleString()}`} /><MetricCard label="League rank" value={`#${liveTeam && liveLeagueRank ? liveLeagueRank.toLocaleString() : manager.league_rank.toLocaleString()}`} detail={liveTeam && live?.league ? `${live.league.name} · ${live.league.rank_count.toLocaleString()} teams` : "Latest completed snapshot"} /><MetricCard label="Next deadline" value={`GW${targetGameweek}`} detail={formatMYT(deadline) ?? "Not published"} tone={historical ? "warning" : undefined} /></section>
    <div className="content-grid team-layout"><section className="surface pitch-surface"><div className="section-heading"><div><span>{liveTeam ? "OFFICIAL LIVE STARTING XI" : "CAPTURED STARTING XI"}</span><h2>{historical ? `GW${data.gameweek} review` : liveTeam ? `GW${displayGameweek} team` : "Pitch view"}</h2></div><span className="section-chip">11 + 4</span></div><Pitch squad={squad} bootstrap={data.bootstrap} metaForPick={liveTeam ? playerMeta : undefined} /></section><aside className="insight-rail"><section className="surface insight-card primary"><span>NEXT DEADLINE ACTION</span><h2>{move ? `${move.outgoing.name} → ${move.incoming.name}` : "Set lineup, hold transfer"}</h2><p>{rec?.competitive.phaseReason ?? "No finalized competitor snapshot yet. Use the Assistant for the read-only recommendation."}</p><a href="/assistant">Open decision board</a></section><section className="surface insight-card"><span>PLANNING TARGET</span><strong>GW{targetGameweek}</strong><p>Use this target across Assistant, Planner and Players. Every change is applied manually in FPL.</p></section><section className="surface insight-card"><span>AVAILABILITY</span><strong>{flagged.length ? "Check flags" : "All clear"}</strong><p>{flagged.join(", ") || "No flagged players in your current squad."}</p></section></aside></div>
  </div>;
}
