import "server-only";

const API_BASE = (process.env.FPL_API_BASE_URL ?? "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app").replace(/\/$/, "");

export interface V5Minutes { p_start: number; p_bench_appearance: number; expected_minutes: number; p_60_plus: number }
export interface V5Player {
  element: number; name: string; team: string; position: "GKP" | "DEF" | "MID" | "FWD";
  xpts_mean: number; p10: number; p50: number; p90: number; p_return: number; p_10_plus: number;
  expected_minutes: V5Minutes; components: Record<string, number>; source: string; quality_issues: string[];
}
export interface V5Payload {
  available: boolean; gameweek: number; projection_version: string; generated_at?: string;
  quality_status: string; players: V5Player[]; reason?: string;
}

export async function getV5Projections(): Promise<V5Payload> {
  try {
    const response = await fetch(`${API_BASE}/v1/projections/current`, { cache: "no-store" });
    if (!response.ok) throw new Error(`API ${response.status}`);
    const raw = await response.json() as {
      gameweek: number; projection_version: string;
      meta?: { generated_at?: string; quality_status?: string }; players?: V5Player[];
    };
    return { available: true, gameweek: raw.gameweek, projection_version: raw.projection_version,
      generated_at: raw.meta?.generated_at, quality_status: raw.meta?.quality_status ?? "unknown", players: raw.players ?? [] };
  } catch (error) {
    return { available: false, gameweek: 0, projection_version: "projection-v5.0-lab",
      quality_status: "unavailable", players: [], reason: error instanceof Error ? error.message : "Unknown API error" };
  }
}
