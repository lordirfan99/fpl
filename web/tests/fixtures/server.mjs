import { createServer } from "node:http";
import { packet } from "./decision-packet.mjs";
let mode = "normal";
let calls = [];
function leagueContext() {
  const now = new Date().toISOString();
  return { schema_version: 1, league_id: 58005, gameweek: 3, status: "ready", snapshot_at: now,
    freshness: { freshness_hours: 0, stale: false, max_age_hours: 12 },
    goal: { available: true, manager_count: 100, cutoff_rank: 10, cutoff_points: 220, owner_points: 214, owner_rank: 18, points_gap: 6, tied_cutoff: false, inside_target: false },
    history: [{ gameweek: 1, points_gap: 13, snapshot_at: now }, { gameweek: 2, points_gap: null, snapshot_at: null }, { gameweek: 3, points_gap: 6, snapshot_at: now }],
    ownership: { sample_count: 100, population: 100, cohort_rank_threshold: 10, cohort_count: 10, cohort_sample: 10,
      rows: [{ element: 16, name: "Test Player 16", league_pct: 48, target_pct: 80, target_captain_pct: 10, owned_at_snapshot: false },
        { element: 6, name: "Test Player 6", league_pct: 67, target_pct: 70, target_captain_pct: 50, owned_at_snapshot: true }] },
    source: "synthetic-test-fixture", scope: "public_gameweek_research", writes_enabled: false };
}
createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/") { res.end('{"status":"ok"}'); return; }
  if (req.url.startsWith("/__test/mode/")) { mode = req.url.split("/").at(-1); calls = []; res.end("{}"); return; }
  if (req.url === "/__test/calls") { res.end(JSON.stringify(calls)); return; }
  calls.push(req.url);
  if (req.url === "/v1/catalog/compact") {
    if (mode === "stalled-catalog") { res.writeHead(200); res.write('{"players":'); return; }
    res.end(JSON.stringify({ meta: { snapshot_at: new Date().toISOString(), stale: false, quality_status: "valid" },
      players: packet().players.map(p => ({ id: p.id, web_name: p.name, team: p.club, element_type: p.position === "GKP" ? 1 : p.position === "DEF" ? 2 : p.position === "MID" ? 3 : 4, now_cost: p.cost, ep_next: "5.1", form: "4.0", selected_by_percent: "10.0", status: "a", news: "" })),
      teams: packet().teams, events: [{ id: 3, is_current: true, finished: false }, { id: 4, is_next: true, finished: false, deadline_time: new Date(Date.now() + 86400000).toISOString() }] })); return;
  }
  if (/^\/v1\/leagues\/(58005|131997)\/decision-context(?:\?.*)?$/.test(req.url)) {
    const data = { ...leagueContext(), league_id: Number(req.url.split("/")[3]) };
    if (mode === "stale-league") { data.freshness.stale = true; data.status = "historical"; }
    res.end(JSON.stringify(data)); return;
  }
  // Production serves finalized standings only. A live GW returns 409 until
  // the journal/snapshot pipeline is complete; simulate the exact boundary.
  const url = new URL(req.url, "http://127.0.0.1:4185");
  const leagueSummary = url.pathname.match(/^\/v1\/leagues\/(58005|131997)\/summary$/);
  const leagueDirectory = url.pathname.match(/^\/v1\/leagues\/(58005|131997)\/directory$/);
  const managerDetail = url.pathname.match(/^\/v1\/leagues\/(58005|131997)\/managers\/(\d+)$/);
  const manager = id => ({ entry_id: id, entry_name: `Test FC ${id}`, player_name: `Manager ${id}`,
    gw_points: id === 2797967 ? 56 : 61, total_points: 340, overall_rank: 12000,
    league_rank: id === 2797967 ? 2 : 1, squad_cost: 100.5, captain: "Test Player 1", transfers_made: 1,
    squad: Array.from({ length: 15 }, (_, index) => ({ element: index + 1, name: `Test Player ${index + 1}`,
      position: index === 0 || index === 11 ? "GKP" : index < 6 ? "DEF" : index < 11 ? "MID" : "FWD",
      team: "Test Club", cost: 5, multiplier: index < 11 ? 1 : 0,
      is_captain: index === 0, is_vice_captain: index === 1 })) });
  const fullLeague = url.pathname.match(/^\/v1\/leagues\/(58005|131997)(?:\/compact)?$/);
  if (url.pathname.endsWith("/live/status")) { res.end(JSON.stringify(url.pathname.includes("131997") ? { gameweek: 2, ready: false } : { gameweek: 3, ready: true })); return; }
  if (fullLeague) {
    if (url.searchParams.get("gw") !== "2") { res.statusCode = 409; res.end('{"detail":"snapshot_not_finalized"}'); return; }
    const managers = [manager(2797967), manager(101010)];
    const body = { meta: { snapshot_at: new Date().toISOString() }, managers };
    if (url.pathname.endsWith("/compact")) {
      body.encoding = "league-squads-v1"; body.gameweek = 2; body.count = 2; body.declared_count = 2;
      body.players = managers[0].squad.map(({ multiplier, is_captain, is_vice_captain, ...player }) => player);
      body.managers = managers.map(row => ({ ...row, squad: row.squad.map((pick, index) => [index, pick.multiplier, pick.is_captain, pick.is_vice_captain]) }));
    }
    res.end(JSON.stringify(body)); return;
  }
  if (url.pathname === "/v1/fixtures") { res.end(JSON.stringify({ gameweeks: {} })); return; }
  if (url.pathname === "/v1/journal") {
    res.end(JSON.stringify({ schema_version: 1, season: "2026-27", gameweeks: [], totals: { completed: 0, points: 0 } })); return;
  }
  if (leagueSummary || leagueDirectory || managerDetail) {
    if (url.searchParams.get("gw") !== "2") {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: "snapshot_not_finalized" }));
      return;
    }
    const entries = [manager(2797967), manager(101010)];
    if (leagueSummary) {
      const managers = entries.map(({ squad, ...row }) => row);
      res.end(JSON.stringify({ league_id: Number(leagueSummary[1]), gameweek: 2, total: 2, filtered_total: 2,
        page: 1, page_size: 50, pages: 1, query: url.searchParams.get("q") ?? "",
        average_gameweek_points: 58.5, leader: managers[1], manager: managers[0],
        managers, meta: { snapshot_at: new Date().toISOString() } }));
      return;
    }
    if (leagueDirectory) {
      res.end(JSON.stringify({ gameweek: 2, managers: entries.map(({ squad, ...row }) => row) }));
      return;
    }
    res.end(JSON.stringify(entries.find(row => row.entry_id === Number(managerDetail[2])) ?? entries[0]));
    return;
  }
  if (req.url === "/v1/private/dashboard/current" && req.headers.authorization === `Bearer ${"test-read-only-".repeat(4)}`) {
    const value = packet();
    if (mode === "hold") { value.transfers = []; value.action = "HOLD"; value.starters = Array.from({ length: 11 }, (_, i) => i + 1); value.bench = [12,13,14,15]; value.bank_after = 1; }
    if (["wildcard", "freehit"].includes(mode)) value.chip = mode;
    const response = JSON.stringify(mode === "unavailable" ? { status: "unavailable", packet: null } : { status: "ready", packet: value, account_checked_at: new Date().toISOString() });
    if (mode === "stalled-private") { res.writeHead(200); res.write('{"status":'); }
    else if (mode === "slow-private") setTimeout(() => res.end(response), 2500);
    else res.end(response);
  } else { res.statusCode = 404; res.end("{}"); }
}).listen(4185, "127.0.0.1");

