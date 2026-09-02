import type { LineupPlayer } from "@/lib/lineup";
import type { Bootstrap, Pick, Position } from "@/lib/types";
import { PlayerCard } from "./player-card";

const order: Position[] = ["GKP", "DEF", "MID", "FWD"];

function positionOf(player: LineupPlayer): Position {
  const value = player.position ?? player.pos;
  return value === "GKP" || value === "DEF" || value === "FWD" ? value : "MID";
}

function toPick(player: LineupPlayer, index: number, captainId?: number, viceId?: number): Pick {
  const id = Number(player.id);
  return {
    element: id,
    name: player.name ?? `Player ${id}`,
    position: positionOf(player),
    team: String(player.club ?? ""),
    cost: Number(player.cost ?? 0),
    multiplier: id === captainId ? 2 : index < 11 ? 1 : 0,
    is_captain: id === captainId,
    is_vice_captain: id === viceId,
  };
}

function projected(player: LineupPlayer) {
  return typeof player.xpts === "number" ? `${player.xpts.toFixed(1)} xPts` : "xPts —";
}

export function ProjectionPitch({ lineup, bench, captainId, viceId, bootstrap }: { lineup: LineupPlayer[]; bench: LineupPlayer[]; captainId?: number; viceId?: number; bootstrap: Bootstrap }) {
  const starters = lineup.map((player, index) => ({ player, pick: toPick(player, index, captainId, viceId) }));
  const substitutes = bench.map((player, index) => ({ player, pick: toPick(player, index + 11, captainId, viceId) }));
  return <div className="squad-view projection-squad"><div className="pitch" aria-label="Projected starting eleven football pitch">{order.map((position) => <div className={`pitch-row ${position.toLowerCase()}`} key={position}>{starters.filter(({ pick }) => pick.position === position).map(({ player, pick }) => <PlayerCard key={pick.element} pick={pick} bootstrap={bootstrap} meta={projected(player)} />)}</div>)}</div><div className="bench"><div className="bench-heading"><span>Bench order</span><small>{substitutes.length} substitutes</small></div><div className="bench-grid">{substitutes.map(({ player, pick }) => <PlayerCard key={pick.element} pick={pick} bootstrap={bootstrap} meta={projected(player)} />)}</div></div></div>;
}
