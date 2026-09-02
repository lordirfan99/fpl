import "server-only";

const API_BASE = (process.env.FPL_API_BASE_URL ?? "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app").replace(/\/$/, "");

export type LiveTeam = {
  source: "official-fpl-live";
  status: "live";
  gameweek: number;
  fetched_at: string;
  provisional: boolean;
  entry: { id: number; entry_name: string; player_name: string; overall_rank: number; total_points: number; value: number; bank: number; transfers_made: number; transfers_cost: number };
  league: { id: number; name: string; entry_rank: number; entry_last_rank: number; rank_count: number } | null;
  picks: Array<{ element: number; position: number; multiplier: number; is_captain: boolean; is_vice_captain: boolean; web_name: string; team: number; points: number; now_cost: number }>;
  fixtures: Array<{ team_h: number; team_a: number; started: boolean; finished: boolean; kickoff_time: string | null }>;
  points: number;
  points_source: "history" | "official-live-picks";
};

export async function getLiveTeam(gameweek?: number, leagueId = 58005): Promise<LiveTeam | null> {
  const params = new URLSearchParams({ league_id: String(leagueId) });
  if (gameweek) params.set("gw", String(gameweek));
  try {
    const response = await fetch(`${API_BASE}/v1/live/team?${params}`, { cache: "no-store" });
    if (!response.ok) return null;
    return await response.json() as LiveTeam;
  } catch {
    return null;
  }
}
