import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { StaticDataset, staticFreshness } from "../../lib/static-dataset";
import type { CompactLeague } from "../../lib/compact-league";

function fixture() {
  const now = new Date().toISOString();
  const league: CompactLeague & { league_id: number } = { encoding: "league-squads-v1", league_id: 58005,
    count: 2, declared_count: 2, gameweek: 6, provisional: false, meta: { snapshot_at: now },
    players: [{ element: 1, name: "Captured Player", position: "MID", team: "Recorded Club", cost: 5 }],
    managers: [2797967, 123].map((id, index) => ({ entry_id: id, entry_name: index ? "Rival FC" : "Owner FC", player_name: "Manager",
      league_rank: index + 1, overall_rank: 1000, gw_points: 50, total_points: 300, squad_cost: 75, captain: "Captured Player", transfers_made: 0,
      squad: Array.from({ length: 15 }, (_, i) => [0, i < 11 ? 1 : 0, i === 0, i === 1]) })) };
  const bodies = new Map<string, string>([
    ["leagues/58005/gw/6.json", JSON.stringify(league)],
    ["leagues/58005/live.json", JSON.stringify({ ...league, provisional: true })],
    ["catalog.json", JSON.stringify({ meta: { snapshot_at: now }, players: [], teams: [], events: [] })],
    ["projections.json", JSON.stringify({ meta: { snapshot_at: now }, players: [], gameweek: 7 })],
    ["leagues/58005/team.json", JSON.stringify({ fetched_at: now, gameweek: 6 })],
    ["leagues/58005/research.json", JSON.stringify({ gameweek: 6, freshness: { snapshot_at: now }, transfers: [1], captains: [1] })],
  ]);
  const manifest = { schema_version: 1, release: "a".repeat(64), revision: "b".repeat(40), generated_at: now,
    writes_enabled: false, gameweeks: { "58005": [6], "131997": [] },
    files: Object.fromEntries([...bodies].map(([p, body]) => [p, createHash("sha256").update(body).digest("hex")])) };
  const calls: string[] = [];
  const fetcher = async (url: string) => {
    calls.push(url);
    if (url.endsWith("/manifest.json")) return Response.json(manifest);
    const path = url.split(`/releases/${manifest.release}/`)[1];
    return new Response(bodies.get(path) ?? "missing", { status: bodies.has(path) ? 200 : 404 });
  };
  const adapter = new StaticDataset("https://example.test/fpl", fetcher);
  const request = (path: string) => adapter.request(new URL(`https://vm.test/fpl-scout-api${path}`));
  return { bodies, manifest, calls, request };
}

test("search and manager selection use only a verified static archive", async () => {
  const f = fixture();
  const response = await f.request("/v1/leagues/58005/summary?q=rival&page=1");
  const summary = await response.json();
  expect(response.status).toBe(200);
  expect(summary.filtered_total).toBe(1);
  expect(summary.managers[0].entry_name).toBe("Rival FC");
  expect(summary.managers[0].squad).toBeUndefined();
  const detail = await (await f.request("/v1/leagues/58005/managers/123?gw=6")).json();
  expect(detail.squad).toHaveLength(15);
  expect(f.calls).toHaveLength(2);
  expect(f.calls.every(url => url.startsWith("https://example.test/fpl/"))).toBe(true);
});

test("manifest selects available history without sequential missing cloud reads", async () => {
  const f = fixture();
  expect((await (await f.request("/v1/leagues/58005/summary?gw=8")).json()).gameweek).toBe(6);
  expect(f.calls).toHaveLength(2);
  expect((await f.request("/v1/leagues/58005/compact?gw=8")).status).toBe(404);
  expect(f.calls).toHaveLength(2);
});

test("corrupt or mismatched artifacts fail closed", async () => {
  const f = fixture();
  f.bodies.set("catalog.json", '{"players":["corrupt"]}');
  const response = await f.request("/v1/catalog/compact");
  expect(response.status).toBe(503);
  expect((await response.json()).detail).toContain("integrity");
});

test("private routes never touch the public host", async () => {
  const f = fixture();
  expect((await f.request("/v1/private/dashboard/current")).status).toBe(404);
  expect(f.calls).toEqual([]);
});

test("old captures become unavailable advice without renewed timestamps", async () => {
  const f = fixture(), old = "2020-01-01T00:00:00Z";
  const raw = JSON.stringify({ gameweek: 6, freshness: { snapshot_at: old }, transfers: [1], captains: [1] });
  f.bodies.set("leagues/58005/research.json", raw);
  f.manifest.files["leagues/58005/research.json"] = createHash("sha256").update(raw).digest("hex");
  const result = await (await f.request("/v1/decision/current?league_id=58005")).json();
  expect(result.freshness).toMatchObject({ stale: true, snapshot_at: old, account_state_verified: false });
  expect(result).toMatchObject({ packet_status: "needs_refresh", transfers: [], captains: [], plan: null, writes_enabled: false, executable: false });
  expect(staticFreshness(old).stale).toBe(true);
});

test("missing requested manager history never substitutes a different gameweek", async () => {
  const f = fixture();
  expect((await f.request("/v1/leagues/58005/managers/123?gw=5")).status).toBe(404);
});
