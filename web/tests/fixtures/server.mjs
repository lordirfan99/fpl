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
