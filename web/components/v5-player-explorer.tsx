"use client";

import { useDeferredValue, useState } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import type { V5Player } from "@/lib/v5";

const positions = ["ALL", "GKP", "DEF", "MID", "FWD"] as const;

export function V5PlayerExplorer({ players }: { players: V5Player[] }) {
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState<(typeof positions)[number]>("ALL");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const visible = players.filter((player) =>
    (position === "ALL" || player.position === position) &&
    (!deferredQuery || `${player.name} ${player.team}`.toLowerCase().includes(deferredQuery)),
  ).slice(0, 80);

  return <section className="surface v5-explorer">
    <div className="section-heading"><div><span>PLAYER EXPLORER</span><h2>Independent projection board</h2></div><span className="section-chip">{players.length} player universe</span></div>
    <div className="v5-toolbar">
      <label><Search size={16} /><span className="sr-only">Search players</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search player or club…" /></label>
      <div className="v5-position-tabs" aria-label="Filter by position"><SlidersHorizontal size={15} />{positions.map((item) => <button key={item} type="button" aria-pressed={position === item} onClick={() => setPosition(item)} className={position === item ? "active" : ""}>{item}</button>)}</div>
    </div>
    <div className="v5-card-grid">
      {visible.map((player, index) => <article className="v5-player" key={player.element}>
        <header><span className="v5-rank">{String(index + 1).padStart(2, "0")}</span><div><strong>{player.name}</strong><small>{player.team} · {player.position}</small></div><b>{player.xpts_mean.toFixed(1)}<small>xPts</small></b></header>
        <div className="v5-range"><span style={{ left: `${Math.min(90, Math.max(4, player.p10 * 5))}%` }} /><i /><span style={{ left: `${Math.min(96, player.p90 * 5)}%` }} /><b style={{ left: `${Math.min(94, player.p50 * 5)}%` }} /></div>
        <div className="v5-stats"><span><b>{player.p10.toFixed(1)}</b>Low range</span><span><b>{player.p50.toFixed(1)}</b>Estimate</span><span><b>{player.p90.toFixed(1)}</b>High range</span><span><b>{player.expected_minutes.expected_minutes.toFixed(0)}</b>Minutes</span></div>
        <footer><span>{Math.round(player.p_return * 100)}% return</span><span>{Math.round(player.p_10_plus * 100)}% haul</span>{player.quality_issues.length ? <em>Data caveat</em> : <em className="ready">Ready</em>}</footer>
      </article>)}
    </div>
    {visible.length === 0 ? <div className="empty-state"><h3>No matching players</h3><p>Try another name or position.</p></div> : null}
  </section>;
}
