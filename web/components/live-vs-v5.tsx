import { FlaskConical, ShieldCheck } from "lucide-react";
import { MetricCard } from "@/components/metric-card";
import { ProjectionPitch } from "@/components/projection-pitch";
import type { LineupPlayer } from "@/lib/lineup";
import type { LiveTeam } from "@/lib/live";
import type { Bootstrap } from "@/lib/types";
import type { V5Payload, V5Player } from "@/lib/v5";

const outfield = ["DEF", "MID", "FWD"];

function formation(players: LineupPlayer[]) {
  return outfield.map((position) => players.filter((player) => (player.position ?? player.pos) === position).length).join("-");
}

function namesOnly(players: LineupPlayer[], other: Set<number>) {
  return players.filter((player) => player.id != null && !other.has(player.id)).map((player) => player.name).filter(Boolean) as string[];
}

const legalFormations = [[3, 4, 3], [3, 5, 2], [4, 4, 2], [4, 3, 3], [4, 5, 1], [5, 3, 2], [5, 4, 1], [5, 2, 3]];

function selectV5Lineup(players: V5Player[]) {
  const byPosition = (position: string) => players.filter((player) => player.position === position).toSorted((a, b) => b.xpts_mean - a.xpts_mean);
  const goalkeepers = byPosition("GKP"), defenders = byPosition("DEF"), midfielders = byPosition("MID"), forwards = byPosition("FWD");
  let best: V5Player[] = [];
  for (const [def, mid, fwd] of legalFormations) {
    const lineup = [...goalkeepers.slice(0, 1), ...defenders.slice(0, def), ...midfielders.slice(0, mid), ...forwards.slice(0, fwd)];
    if (lineup.length === 11 && lineup.reduce((sum, player) => sum + player.xpts_mean, 0) > best.reduce((sum, player) => sum + player.xpts_mean, 0)) best = lineup;
  }
  const selected = new Set(best.map((player) => player.element));
  const bench = [...goalkeepers.filter((player) => !selected.has(player.element)), ...players.filter((player) => player.position !== "GKP" && !selected.has(player.element)).toSorted((a, b) => b.xpts_mean - a.xpts_mean)];
  const captain = best.toSorted((a, b) => b.xpts_mean - a.xpts_mean)[0];
  const convert = (player: V5Player): LineupPlayer => ({ id: player.element, name: player.name, position: player.position, club: player.team, xpts: player.xpts_mean });
  return { lineup: best.map(convert), bench: bench.slice(0, 4).map(convert), captainId: captain?.element, points: best.reduce((sum, player) => sum + player.xpts_mean, captain?.xpts_mean ?? 0), robustPoints: best.reduce((sum, player) => sum + player.p10, captain?.p10 ?? 0) };
}

function ModelXi({ label, version, mode, lineup, bench, captainId, viceId, points, robustPoints, unique, uniqueLabel, transferNote, bootstrap }: { label: string; version: string; mode: "live" | "lab"; lineup: LineupPlayer[]; bench: LineupPlayer[]; captainId?: number; viceId?: number; points: number; robustPoints?: number; unique: string[]; uniqueLabel: string; transferNote: string; bootstrap: Bootstrap }) {
  return <section className={`surface model-xi ${mode}`}>
    <div className="section-heading"><div><span>{label}</span><h2>{version}</h2><p>{mode === "live" ? "Your current official 15, shown with V5 model xPts. Read-only — apply changes yourself in FPL." : "Legal XI the V5 lab would pick from your live 15. Research only; it cannot execute or promote itself."}</p></div><span className="section-chip">{mode === "live" ? <><ShieldCheck size={12} /> OFFICIAL LIVE</> : <><FlaskConical size={12} /> LAB ONLY</>}</span></div>
    <div className="model-xi-stats"><div><span>Formation</span><strong>{formation(lineup)}</strong></div><div><span>XI + captain</span><strong>{points.toFixed(1)}</strong></div><div><span>Robust score</span><strong>{robustPoints == null ? "—" : robustPoints.toFixed(1)}</strong></div><div><span>Captain</span><strong>{lineup.find((player) => player.id === captainId)?.name ?? "—"}</strong></div></div>
    <ProjectionPitch lineup={lineup} bench={bench} captainId={captainId} viceId={viceId} bootstrap={bootstrap} />
    <div className="model-xi-evidence"><div><span>{uniqueLabel}</span><p>{unique.join(", ") || "None — the compared XIs match."}</p></div><div><span>TRANSFERS</span><p>{transferNote}</p></div></div>
  </section>;
}

export function LiveVsV5({ live, v5, bootstrap, nextDeadlineGw }: { live: LiveTeam | null; v5: V5Payload; bootstrap: Bootstrap; nextDeadlineGw: number }) {
  const livePicks = live?.picks?.length === 15 ? [...live.picks].sort((a, b) => a.position - b.position) : [];
  const v5ById = new Map(v5.players.map((player) => [player.element, player]));
  const toLineup = (pick: (typeof livePicks)[number]): LineupPlayer => {
    const meta = bootstrap.elements.find((row) => row.id === pick.element);
    const position = meta?.element_type === 1 ? "GKP" : meta?.element_type === 2 ? "DEF" : meta?.element_type === 3 ? "MID" : "FWD";
    return { id: pick.element, name: pick.web_name, position, club: pick.team, xpts: v5ById.get(pick.element)?.xpts_mean };
  };
  const liveLineup = livePicks.slice(0, 11).map(toLineup);
  const liveBench = livePicks.slice(11).map(toLineup);
  const liveCaptain = livePicks.find((pick) => pick.is_captain)?.element;
  const liveVice = livePicks.find((pick) => pick.is_vice_captain)?.element;
  const liveReady = liveLineup.length === 11 && liveBench.length === 4;
  const livePoints = liveLineup.reduce((sum, player) => sum + Number(player.xpts ?? 0), 0) + Number(liveLineup.find((player) => player.id === liveCaptain)?.xpts ?? 0);
  const liveIds = new Set(liveLineup.flatMap((player) => player.id == null ? [] : [player.id]));

  const liveSquadIds = new Set(livePicks.map((pick) => pick.element));
  const v5Comparable = v5.available && v5.gameweek === nextDeadlineGw;
  const v5Selection = v5Comparable ? selectV5Lineup(v5.players.filter((player) => liveSquadIds.has(player.element))) : null;
  const v5Ready = v5Selection?.lineup.length === 11 && v5Selection.bench.length === 4;
  const v5Ids = new Set((v5Selection?.lineup ?? []).flatMap((player) => player.id == null ? [] : [player.id]));
  const overlap = v5Ready ? [...liveIds].filter((id) => v5Ids.has(id)).length : 0;

  return <div className="page-stack">
    <section className="section-heading"><div><span>MODEL XI COMPARISON</span><h2>Your live XI vs the V5 lineup lab</h2><p>Same 15 players, two selections. A difference is a model disagreement, not proof of more points. Read-only — every change is made manually in FPL.</p></div></section>
    <section className="metric-grid"><MetricCard label="Live XI xPts" value={liveReady ? livePoints.toFixed(1) : "—"} detail="Your current 15" tone="positive" /><MetricCard label="V5 lineup lab" value={v5Ready ? formation(v5Selection?.lineup ?? []) : "Unavailable"} detail={v5.projection_version} tone={v5Ready ? "warning" : undefined} /><MetricCard label="XI overlap" value={v5Ready ? `${overlap}/11` : "—"} detail="Live versus V5 lab" /><MetricCard label="Target gameweek" value={`GW${nextDeadlineGw}`} detail={v5Comparable ? "V5 aligned" : "V5 targets another GW"} /></section>
    {liveReady ? <div className="model-compare-grid">
      <ModelXi label="YOUR LIVE XI" version={`GW${live?.gameweek ?? nextDeadlineGw} squad`} mode="live" lineup={liveLineup} bench={liveBench} captainId={liveCaptain} viceId={liveVice} points={livePoints} unique={v5Ready ? namesOnly(liveLineup, v5Ids) : []} uniqueLabel="UNIQUE VS V5 LAB XI" transferNote="Your real squad — no model transfers applied." bootstrap={bootstrap} />
      {v5Ready && v5Selection ? <ModelXi label="V5 LINEUP LAB" version={v5.projection_version} mode="lab" lineup={v5Selection.lineup} bench={v5Selection.bench} captainId={v5Selection.captainId} points={v5Selection.points} robustPoints={v5Selection.robustPoints} unique={namesOnly(v5Selection.lineup, liveIds)} uniqueLabel="UNIQUE VS LIVE XI" transferNote="Not modelled — the V5 lab optimises the XI from your live 15 only." bootstrap={bootstrap} /> : <section className="surface model-xi-empty"><FlaskConical /><h2>No comparable V5 lineup</h2><p>{v5.available && v5.gameweek !== nextDeadlineGw ? `V5 targets GW${v5.gameweek}; the next deadline is GW${nextDeadlineGw}. Cross-gameweek comparison is blocked.` : v5.reason ?? "V5 does not have projections for all 15 of your live-squad players."}</p></section>}
    </div> : <section className="surface model-xi-empty"><ShieldCheck /><h2>Live squad unavailable</h2><p>The official FPL live squad (15 picks) could not be read. Model comparison needs your current team as the baseline.</p></section>}
  </div>;
}
