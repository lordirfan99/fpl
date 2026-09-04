import { createServer } from "node:http";
import { packet } from "./decision-packet.mjs";
let mode = "normal";
createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/") { res.end('{"status":"ok"}'); return; }
  if (req.url.startsWith("/__test/mode/")) { mode = req.url.split("/").at(-1); res.end("{}"); return; }
  if (req.url === "/v1/private/dashboard/current" && req.headers.authorization === `Bearer ${"test-read-only-".repeat(4)}`) {
    const value = packet();
    if (["wildcard", "freehit"].includes(mode)) value.chip = mode;
    res.end(JSON.stringify(mode === "unavailable" ? { status: "unavailable", packet: null } : { status: "ready", packet: value, account_checked_at: new Date().toISOString() }));
  } else { res.statusCode = 404; res.end("{}"); }
}).listen(4185, "127.0.0.1");
