import "server-only";
export type LeagueDecisionContext = {
  schema_version: number; league_id: number; gameweek?: number; status: string; snapshot_at?: string;
  goal: { available: boolean; manager_count?: number; cutoff_rank?: number; cutoff_points?: number; owner_points?: number; owner_rank?: number; points_gap?: number; tied_cutoff?: boolean; inside_target?: boolean };
  history: { gameweek: number; points_gap: number | null; snapshot_at: string | null }[];
  ownership: { rows: { element: number; name: string; position?: string; team?: string; league_pct: number; target_pct: number | null; target_captain_pct: number | null; owned_at_snapshot: boolean | null }[]; sample_count?: number; population?: number; cohort_rank_threshold?: number; cohort_count?: number; cohort_sample?: number };
};
export async function getLeagueDecision(league: number): Promise<LeagueDecisionContext | null> {
  try {
    const base = process.env.FPL_API_BASE_URL ?? "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app";
    const response = await fetch(`${base}/v1/leagues/${league}/decision-context`, { cache: "no-store", signal: AbortSignal.timeout(15000) });
    if (!response.ok) return null;
    const value: unknown = await response.json();
    if (!value || typeof value !== "object") return null;
    const candidate = value as Partial<LeagueDecisionContext>;
    if (candidate.schema_version !== 1 || !candidate.goal || !Array.isArray(candidate.history)
      || !candidate.ownership || !Array.isArray(candidate.ownership.rows)) return null;
    return candidate as LeagueDecisionContext;
  } catch { return null; }
}
