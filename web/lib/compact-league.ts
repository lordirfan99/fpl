import type { Manager, Pick } from "./types";

type RecordedPlayer = Omit<Pick, "multiplier" | "is_captain" | "is_vice_captain" | "selected_by"> & { selected_by?: number | null };
export interface CompactLeague {
  encoding: "league-squads-v1";
  meta?: { generated_at?: string; snapshot_at?: string };
  gameweek: number;
  count: number;
  declared_count: number;
  players: RecordedPlayer[];
  managers: Array<Omit<Manager, "squad"> & { squad: Array<[number, number, boolean, boolean]> }>;
  provisional?: boolean;
}

export function decodeCompactLeague(payload: CompactLeague) {
  if (payload.encoding !== "league-squads-v1" || !Array.isArray(payload.players) || !Array.isArray(payload.managers)
      || payload.count !== payload.managers.length || payload.declared_count !== payload.count || payload.count <= 0) {
    throw new Error("Incomplete compact league response");
  }
  const seen = new Set<number>();
  const managers: Manager[] = payload.managers.map(row => {
    if (!Number.isInteger(row.entry_id) || seen.has(row.entry_id) || !Array.isArray(row.squad) || row.squad.length !== 15) {
      throw new Error("Invalid compact league manager");
    }
    seen.add(row.entry_id);
    const squad = row.squad.map(ref => {
      if (!Array.isArray(ref) || ref.length !== 4 || !Number.isInteger(ref[0]) || ref[0] < 0 || ref[0] >= payload.players.length
          || !Number.isInteger(ref[1]) || ref[1] < 0 || typeof ref[2] !== "boolean" || typeof ref[3] !== "boolean") {
        throw new Error("Invalid compact squad reference");
      }
      const player = payload.players[ref[0]];
      return { ...player, selected_by: player.selected_by ?? undefined, multiplier: ref[1], is_captain: ref[2], is_vice_captain: ref[3] };
    });
    return { ...row, squad };
  });
  return { meta: payload.meta, gameweek: payload.gameweek, provisional: payload.provisional, managers };
}
