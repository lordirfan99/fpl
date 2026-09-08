import { test, expect } from "@playwright/test";
import {
  availabilityLabel, fixtureLoad, fixtureRun, metricLeaders, parseNumber,
  resolveCompareIds, shortTeam, toComparePlayer, type ComparePlayer,
} from "../../lib/player-compare";
import type { Bootstrap, BootstrapPlayer, BootstrapTeam } from "../../lib/types";

const TEAMS: BootstrapTeam[] = [
  { id: 1, name: "Arsenal", short_name: "ARS", code: 1 },
  { id: 2, name: "Nott'm Forest", short_name: "NFO", code: 2 },
];
const mkPlayer = (over: Partial<BootstrapPlayer>): BootstrapPlayer => ({
  id: 1, web_name: "Saka", first_name: "B", second_name: "Saka", team: 1, element_type: 3,
  photo: "", now_cost: 90, form: "6.0", points_per_game: "5.0", selected_by_percent: "40.0",
  ep_next: "5.5", event_points: 0, status: "a", chance_of_playing_next_round: null, news: "", ...over,
});
const catalogue = (players: BootstrapPlayer[]): Bootstrap => ({ elements: players, teams: TEAMS, events: [] });

test("parseNumber coerces FPL string fields and never yields NaN", () => {
  expect(parseNumber("5.5")).toBe(5.5);
  expect(parseNumber(null)).toBe(0);
  expect(parseNumber("")).toBe(0);
  expect(parseNumber("abc")).toBe(0);
});

test("shortTeam maps known long names and falls back to a 3-letter code", () => {
  expect(shortTeam("Nott'm Forest")).toBe("NFO");
  expect(shortTeam("Everton")).toBe("EVE");
});

test("resolveCompareIds dedupes, drops unknowns, keeps order, caps at four", () => {
  const cat = catalogue([mkPlayer({ id: 1 }), mkPlayer({ id: 2 }), mkPlayer({ id: 3 }), mkPlayer({ id: 4 }), mkPlayer({ id: 5 })]);
  expect(resolveCompareIds("3,3,999,1,2", cat).map((r) => r.id)).toEqual([3, 1, 2]);
  expect(resolveCompareIds("1,2,3,4,5", cat).map((r) => r.id)).toEqual([1, 2, 3, 4]);
  expect(resolveCompareIds(undefined, cat)).toEqual([]);
});

test("toComparePlayer reads price in millions and resolves the club", () => {
  const row = toComparePlayer(mkPlayer({ now_cost: 91, team: 2 }), new Map(TEAMS.map((t) => [t.id, t])));
  expect(row.price).toBe(9.1);
  expect(row.team).toBe("Nott'm Forest");
  expect(row.teamShort).toBe("NFO");
});

const row = (over: Partial<ComparePlayer>): ComparePlayer => ({
  id: 1, name: "A", team: "Arsenal", teamShort: "ARS", position: "MID", price: 9, epNext: 5,
  form: 6, ownership: 40, status: "a", chanceOfPlaying: null, news: "", ...over,
});

test("metricLeaders returns the best id(s), ties included, and nothing below two rows", () => {
  const rows = [row({ id: 1, epNext: 5, price: 9 }), row({ id: 2, epNext: 7, price: 8 }), row({ id: 3, epNext: 7, price: 9 })];
  expect([...metricLeaders(rows, (r) => r.epNext, "high")].sort()).toEqual([2, 3]);
  expect([...metricLeaders(rows, (r) => r.price, "low")]).toEqual([2]);
  expect(metricLeaders([rows[0]], (r) => r.epNext, "high").size).toBe(0);
});

const HORIZON = {
  gw5: [{ team_h: "Arsenal", team_a: "Nott'm Forest", team_h_difficulty: 2, team_a_difficulty: 4 }],
  gw6: [
    { team_h: "Chelsea", team_a: "Arsenal", team_h_difficulty: 3, team_a_difficulty: 3 },
    { team_h: "Arsenal", team_a: "Everton", team_h_difficulty: 2, team_a_difficulty: 4 },
  ],
  gw7: [],
};

test("fixtureRun keeps home/away and both legs of a double gameweek", () => {
  const run = fixtureRun(row({ team: "Arsenal" }), HORIZON, [5, 6, 7]);
  expect(run[0]).toEqual([{ label: "NFO (H)", fdr: 2 }]);
  expect(run[1]).toEqual([{ label: "CHE (A)", fdr: 3 }, { label: "EVE (H)", fdr: 2 }]);
  expect(run[2]).toEqual([]);
});

test("fixtureLoad sums difficulty and charges a blank week the maximum", () => {
  const run = fixtureRun(row({ team: "Arsenal" }), HORIZON, [5, 6, 7]);
  expect(fixtureLoad(run)).toBe(2 + (3 + 2) + 5);
});

test("availabilityLabel prefers the news line, then chance, then a flag", () => {
  expect(availabilityLabel(row({ status: "a" }))).toBe("Available");
  expect(availabilityLabel(row({ status: "d", news: "Knock - 75% chance" }))).toBe("Knock - 75% chance");
  expect(availabilityLabel(row({ status: "d", news: "", chanceOfPlaying: 50 }))).toBe("50% chance");
  expect(availabilityLabel(row({ status: "i", news: "", chanceOfPlaying: null }))).toBe("Flagged");
});
