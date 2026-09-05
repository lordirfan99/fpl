import { createServer } from "node:http";
import { packet } from "./decision-packet.mjs";
let mode = "normal";
function leagueContext() {
  const now = new Date().toISOString();
  return { schema_version: 1, league_id: 58005, gameweek: 3, status: "ready", snapshot_at: now,
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
  if (req.url.startsWith("/__test/mode/")) { mode = req.url.split("/").at(-1); res.end("{}"); return; }
  if (/^\/v1\/leagues\/(58005|131997)\/decision-context$/.test(req.url)) { res.end(JSON.stringify(leagueContext())); return; }
  if (req.url === "/v1/private/dashboard/current" && req.headers.authorization === `Bearer ${"test-read-only-".repeat(4)}`) {
    const value = packet();
    if (["wildcard", "freehit"].includes(mode)) value.chip = mode;
    res.end(JSON.stringify(mode === "unavailable" ? { status: "unavailable", packet: null } : { status: "ready", packet: value, account_checked_at: new Date().toISOString() }));
  } else { res.statusCode = 404; res.end("{}"); }
}).listen(4185, "127.0.0.1");
