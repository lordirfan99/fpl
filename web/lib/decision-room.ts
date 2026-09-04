export type NumberOrNull = number | null;
export type AccountPick = { element: number; position: number; selling_price: number; is_captain: boolean; is_vice_captain: boolean };
export type EvidencePlayer = {
  id: number; name: string; position: string; club: number | null; cost: NumberOrNull;
  xpts: NumberOrNull; xpts_by_gw: NumberOrNull[] | null; expected_minutes: NumberOrNull;
  facts: { team: number; minutes: NumberOrNull; starts: NumberOrNull; expected_goals: string | null;
    expected_assists: string | null; defensive_contribution: NumberOrNull; saves: NumberOrNull;
    status: string | null; news: string | null; news_added: string | null; chance_of_playing_next_round: NumberOrNull };
};
export type Alternative = { horizon_gain: NumberOrNull; net_after_hit: NumberOrNull; projection_starts_gw: NumberOrNull; moves: { out: string; in: string; hit: boolean }[] };
export type DecisionPacket = {
  schema_version: 1; team_id: number; plan_id: string; gameweek: number; deadline: string; generated_at: string;
  account_fingerprint: string; account: { picks: AccountPick[]; transfers: { bank: number; limit: NumberOrNull; made: NumberOrNull; unlimited: boolean | null }; chips: { name: string; status_for_entry: string; played_by_entry: number[] | null }[] };
  timestamps: { account: string; reference: string; league: string }; model_version: string; chip: string | null;
  bank_after: NumberOrNull; free_transfers_before: NumberOrNull; free_transfers_after: NumberOrNull;
  starters: number[]; bench: number[]; captain: number; vice: number;
  transfers: { element_out: number; element_in: number; out_name: string; in_name: string; hit: boolean; gain: NumberOrNull; package_gain: NumberOrNull }[];
  action: string; reason: string;
  horizon: { metric: string; rows: { gw: number; weight: number; current: NumberOrNull; proposed: NumberOrNull; gain: NumberOrNull }[] };
  alternatives: Record<string, Alternative | null>;
  captains: { id: number; name: string; xpts: NumberOrNull; expected_minutes: NumberOrNull; eligible: boolean; selected: boolean; reason: string }[];
  players: EvidencePlayer[];
  fixtures: { id: number; event: number; team_h: number; team_a: number; team_h_difficulty: number; team_a_difficulty: number; kickoff_time: string | null }[];
  teams: { id: number; short_name: string }[]; writes_enabled: false;
};
export type PrivateDashboard = { status: "ready" | "unavailable" | "signed_out"; packet: DecisionPacket | null; account_checked_at?: string; reasons?: string[] };

export const numeric = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const displayNumber = (value: unknown, decimals = 1) => numeric(value) ? value.toFixed(decimals) : "Unavailable";
export const money = (value: unknown) => numeric(value) ? `£${value.toFixed(1)}m` : "Unavailable";

export function weeklyPoints(packet: DecisionPacket, ids: number[], captain: number | undefined, offset: number) {
  if (ids.length !== 11 || !captain || !ids.includes(captain)) return null;
  const values = ids.map(id => packet.players.find(p => p.id === id)?.xpts_by_gw?.[offset]);
  const cap = packet.players.find(p => p.id === captain)?.xpts_by_gw?.[offset];
  if (!values.every(numeric) || !numeric(cap)) return null;
  // A currently active TC applies only to the first target GW, never the horizon.
  const triple = offset === 0 && packet.account.chips.some(c => c.name === "3xc" && c.status_for_entry === "active");
  return values.reduce((sum, value) => sum + value, 0) + cap * (triple ? 2 : 1);
}

export function fixturesFor(packet: DecisionPacket, player: EvidencePlayer, gw: number) {
  return packet.fixtures.filter(f => f.event === gw && [f.team_h, f.team_a].includes(player.facts.team)).map(f => {
    const home = f.team_h === player.facts.team;
    const opponent = packet.teams.find(t => t.id === (home ? f.team_a : f.team_h));
    return { label: `${opponent?.short_name ?? "Unknown"} (${home ? "H" : "A"})`, fdr: home ? f.team_h_difficulty : f.team_a_difficulty };
  });
}
