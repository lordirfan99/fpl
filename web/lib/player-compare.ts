import type { Bootstrap, BootstrapPlayer, BootstrapTeam, Fixture, FixtureHorizon } from "./types";

export const POSITION_NAME: Record<number, string> = { 1: "GKP", 2: "DEF", 3: "MID", 4: "FWD" };
export const MAX_COMPARE = 4;

// Fixture-horizon rows carry full club names; align them with the compact
// catalogue's short codes the same way the planner does.
const TEAM_ALIASES: Record<string, string> = {
  "Manchester City": "MCI", "Manchester United": "MUN", "Nott'm Forest": "NFO", "Crystal Palace": "CRY",
  "Newcastle United": "NEW", "Ipswich Town": "IPS", "Coventry City": "COV", "Hull City": "HUL",
  "Aston Villa": "AVL", "Wolverhampton Wanderers": "WOL", "Tottenham Hotspur": "TOT",
  "West Ham United": "WHU", "Brighton and Hove Albion": "BHA", "Sheffield United": "SHU",
};

export function shortTeam(team: string): string {
  return TEAM_ALIASES[team] ?? team.slice(0, 3).toUpperCase();
}

export function parseNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export interface ComparePlayer {
  id: number;
  name: string;
  team: string;
  teamShort: string;
  position: string;
  price: number;
  epNext: number;
  form: number;
  ownership: number;
  status: string;
  chanceOfPlaying: number | null;
  news: string;
}

export function toComparePlayer(player: BootstrapPlayer, teamById: Map<number, BootstrapTeam>): ComparePlayer {
  const team = teamById.get(player.team);
  return {
    id: player.id,
    name: player.web_name,
    team: team?.name ?? "—",
    teamShort: team?.short_name ?? "—",
    position: POSITION_NAME[player.element_type] ?? "—",
    price: player.now_cost / 10,
    epNext: parseNumber(player.ep_next),
    form: parseNumber(player.form),
    ownership: parseNumber(player.selected_by_percent),
    status: player.status,
    chanceOfPlaying: player.chance_of_playing_next_round,
    news: player.news ?? "",
  };
}

/** Resolve the requested ids to compare rows, preserving request order, deduped, capped. */
export function resolveCompareIds(raw: string | undefined, catalogue: Bootstrap): ComparePlayer[] {
  const teamById = new Map(catalogue.teams.map((team) => [team.id, team]));
  const byId = new Map(catalogue.elements.map((player) => [player.id, player]));
  const seen = new Set<number>();
  const rows: ComparePlayer[] = [];
  for (const token of (raw ?? "").split(",")) {
    const id = Number(token.trim());
    if (!Number.isInteger(id) || seen.has(id) || !byId.has(id)) continue;
    seen.add(id);
    rows.push(toComparePlayer(byId.get(id)!, teamById));
    if (rows.length >= MAX_COMPARE) break;
  }
  return rows;
}

export interface FixtureCell {
  label: string;
  fdr: number;
}

/** One list of fixture cells per gameweek in `gameweeks` (double gameweeks keep both). */
export function fixtureRun(player: ComparePlayer, horizon: FixtureHorizon, gameweeks: number[]): FixtureCell[][] {
  return gameweeks.map((gameweek) => {
    const fixtures: Fixture[] = horizon[`gw${gameweek}`] ?? [];
    return fixtures
      .filter((fixture) => fixture.team_h === player.team || fixture.team_a === player.team)
      .map((fixture) => {
        const home = fixture.team_h === player.team;
        return {
          label: `${shortTeam(home ? fixture.team_a : fixture.team_h)} (${home ? "H" : "A"})`,
          fdr: home ? fixture.team_h_difficulty : fixture.team_a_difficulty,
        };
      });
  });
}

/** Total fixture difficulty over the horizon; blank weeks count as the hardest (5). */
export function fixtureLoad(run: FixtureCell[][]): number {
  return run.reduce((sum, week) => sum + (week.length ? week.reduce((a, cell) => a + cell.fdr, 0) : 5), 0);
}

export type MetricDirection = "high" | "low";

/** Ids that are the single best on a metric (ties all win); empty when <2 rows. */
export function metricLeaders(rows: ComparePlayer[], value: (row: ComparePlayer) => number, direction: MetricDirection): Set<number> {
  if (rows.length < 2) return new Set();
  const scored = rows.map((row) => ({ id: row.id, v: value(row) }));
  const best = direction === "high" ? Math.max(...scored.map((s) => s.v)) : Math.min(...scored.map((s) => s.v));
  return new Set(scored.filter((s) => s.v === best).map((s) => s.id));
}

export function availabilityLabel(row: ComparePlayer): string {
  if (row.status === "a") return "Available";
  if (row.news) return row.news;
  if (row.chanceOfPlaying != null) return `${row.chanceOfPlaying}% chance`;
  return "Flagged";
}
