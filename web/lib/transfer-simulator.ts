import { numeric, type DecisionPacket, type SimulationPlayer } from "./decision-room";

export type Move = { out: number; in: number };
export type Lineup = { starters: number[]; bench: number[]; captain: number; vice: number; points: number };
export type Simulation = { errors: string[]; bank: number | null; free: number | null; hit: number; weeks: { gw: number; hold: Lineup; draft: Lineup; gain: number }[]; net: number | null };
export const simulationPlayers = (packet: DecisionPacket): SimulationPlayer[] => packet.simulation_players ?? packet.players;
const integerPrice = (value: unknown): value is number => numeric(value) && Number.isInteger(value) && value >= 0;

function bestLineup(squad: SimulationPlayer[], offset: number, boost: boolean, triple: boolean): Lineup | null {
  if (squad.some(p => !numeric(p.xpts_by_gw?.[offset]) || p.xpts_by_gw![offset]! < 0)) return null;
  const points = (p: SimulationPlayer) => p.xpts_by_gw![offset]!;
  const sorted = (position: string) => squad.filter(p => p.position === position).sort((a, b) => points(b) - points(a) || a.id - b.id);
  const keepers = sorted("GKP"), defenders = sorted("DEF"), midfielders = sorted("MID"), forwards = sorted("FWD");
  let best: Lineup | null = null;
  for (let d = 3; d <= 5; d++) for (let m = 2; m <= 5; m++) {
    const f = 10 - d - m;
    if (f < 1 || f > 3 || defenders.length < d || midfielders.length < m || forwards.length < f || !keepers.length) continue;
    const xi = [keepers[0], ...defenders.slice(0, d), ...midfielders.slice(0, m), ...forwards.slice(0, f)];
    const leaders = [...xi].sort((a, b) => points(b) - points(a) || a.id - b.id);
    const score = (boost ? squad : xi).reduce((sum, p) => sum + points(p), 0) + points(leaders[0]) * (triple ? 2 : 1);
    if (!best || score > best.points) {
      const ids = new Set(xi.map(p => p.id));
      best = { starters: xi.map(p => p.id), bench: squad.filter(p => !ids.has(p.id)).sort((a, b) => (a.position === "GKP" ? -1 : b.position === "GKP" ? 1 : points(b) - points(a) || a.id - b.id)).map(p => p.id), captain: leaders[0].id, vice: leaders[1].id, points: score };
    }
  }
  return best;
}

/** Read-only package comparison. Prices are integer tenths; hits occur once. */
export function simulate(packet: DecisionPacket, moves: Move[]): Simulation {
  const result: Simulation = { errors: [], bank: null, free: null, hit: 0, weeks: [], net: null };
  const fail = (message: string) => { result.errors.push(message); return result; };
  const players = new Map(simulationPlayers(packet).map(p => [p.id, p]));
  // Owned-player evidence wins over a duplicate catalog entry.
  for (const p of packet.players) players.set(p.id, p);
  const picks = packet.account.picks, owned = new Set(picks.map(p => p.element));
  if (picks.length !== 15 || owned.size !== 15 || picks.some(p => !players.has(p.element))) return fail("Complete verified squad required.");
  const active = (name: string) => packet.account.chips.some(c => c.name === name && c.status_for_entry === "active");
  const unlimited = packet.account.transfers.unlimited === true || packet.account.transfers.status === "unlimited";
  if (unlimited && !active("wildcard") && !active("freehit")) return fail("Unlimited transfer status needs an active chip.");
  const { limit, made, bank } = packet.account.transfers;
  if (!integerPrice(bank) || (!unlimited && (!integerPrice(limit) || !integerPrice(made)))) return fail("Verified bank and free transfers required.");
  result.free = unlimited ? null : Math.max(0, limit! - made!);
  if (new Set(moves.map(m => m.out)).size !== moves.length || new Set(moves.map(m => m.in)).size !== moves.length) return fail("A player can appear only once in a transfer package.");
  const outs = new Set(moves.map(m => m.out));
  const remaining = picks.filter(p => !outs.has(p.element)).map(p => players.get(p.element)!);
  let nextBank = bank;
  for (const move of moves) {
    const pick = picks.find(p => p.element === move.out), incoming = players.get(move.in), outgoing = players.get(move.out);
    if (!pick || !incoming || !outgoing || move.in === move.out || owned.has(move.in)) return fail("Choose an owned outgoing player and an unowned incoming player.");
    if (incoming.position !== outgoing.position) return fail("Each replacement must have the same position.");
    if (!integerPrice(pick.selling_price) || !integerPrice(incoming.cost)) return fail("Verified selling and buying prices required.");
    nextBank += pick.selling_price - incoming.cost;
    remaining.push(incoming);
  }
  result.bank = nextBank / 10;
  if (nextBank < 0) return fail("This package exceeds your available budget.");
  for (const [position, count] of [["GKP", 2], ["DEF", 5], ["MID", 5], ["FWD", 3]] as const) if (remaining.filter(p => p.position === position).length !== count) return fail("Squad must contain 2 goalkeepers, 5 defenders, 5 midfielders and 3 forwards.");
  const clubs = new Map<number, number>();
  for (const p of remaining) {
    if (!Number.isInteger(p.club) || !p.club || p.club < 1) return fail("Club information is unavailable.");
    clubs.set(p.club, (clubs.get(p.club) ?? 0) + 1);
  }
  if (moves.length && [...clubs.values()].some(count => count > 3)) return fail("Maximum three players from one club.");
  result.hit = unlimited ? 0 : Math.max(0, moves.length - result.free!) * 4;
  const horizon = active("freehit") || packet.chip === "freehit" ? 1 : Math.min(3, 39 - packet.gameweek);
  const original = picks.map(p => players.get(p.element)!);
  for (let offset = 0; offset < horizon; offset++) {
    const hold = bestLineup(original, offset, offset === 0 && active("bboost"), offset === 0 && active("3xc"));
    const draft = bestLineup(remaining, offset, offset === 0 && active("bboost"), offset === 0 && active("3xc"));
    if (!hold || !draft) return fail(`GW${packet.gameweek + offset} projections are unavailable; no gain can be claimed.`);
    result.weeks.push({ gw: packet.gameweek + offset, hold, draft, gain: draft.points - hold.points });
  }
  if (!result.weeks.length) return fail("No remaining gameweek projections.");
  result.net = result.weeks.reduce((sum, week) => sum + week.gain, 0) - result.hit;
  return result;
}
