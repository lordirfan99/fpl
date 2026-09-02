"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";

type Player = { id: number; web_name: string; team: number; element_type: number; now_cost: number; ep_next: string; form: string; selected_by_percent: string; status: string; news: string };
type Team = { id: number; short_name: string };
const positions = ["ALL", "GKP", "DEF", "MID", "FWD"] as const;
const positionName: Record<number, string> = { 1: "GKP", 2: "DEF", 3: "MID", 4: "FWD" };

export function PlayerExplorer({ players, teams, targetGameweek }: { players: Player[]; teams: Team[]; targetGameweek?: number }) {
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState<(typeof positions)[number]>("ALL");
  const [status, setStatus] = useState<"all" | "available" | "flagged">("all");
  const teamNames = useMemo(() => new Map(teams.map((team) => [team.id, team.short_name])), [teams]);
  const filtered = useMemo(() => players.filter((player) => {
    const matchesQuery = player.web_name.toLowerCase().includes(query.trim().toLowerCase()) || (teamNames.get(player.team) ?? "").toLowerCase().includes(query.trim().toLowerCase());
    const matchesPosition = position === "ALL" || positionName[player.element_type] === position;
    const matchesStatus = status === "all" || (status === "available" ? player.status === "a" : player.status !== "a");
    return matchesQuery && matchesPosition && matchesStatus;
  }).sort((a, b) => Number(b.ep_next) - Number(a.ep_next)).slice(0, 100), [players, query, position, status, teamNames]);

  return <section className="surface table-surface"><div className="section-heading"><div><span>PLAYER RESEARCH</span><h2>Search the market</h2><p>xPts is the official FPL next-round estimate, shown for planning GW{targetGameweek ?? "—"}; it is not a guarantee.</p></div><span className="section-chip">{filtered.length} shown</span></div><div className="player-toolbar"><label><Search size={15} /><span className="sr-only">Search players or teams</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search player or club" /></label><div className="filter-tabs" aria-label="Filter by position">{positions.map((item) => <button type="button" key={item} onClick={() => setPosition(item)} className={position === item ? "active" : ""}>{item}</button>)}</div><select aria-label="Filter player availability" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}><option value="all">All availability</option><option value="available">Available</option><option value="flagged">Flagged</option></select></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Player</th><th>Pos</th><th>Team</th><th>Price</th><th>xPts</th><th>Form</th><th>Ownership</th><th>Status</th></tr></thead><tbody>{filtered.map((player) => <tr key={player.id}><td><strong>{player.web_name}</strong></td><td>{positionName[player.element_type]}</td><td>{teamNames.get(player.team) ?? "—"}</td><td>£{(player.now_cost / 10).toFixed(1)}m</td><td><strong>{Number(player.ep_next).toFixed(1)}</strong></td><td>{Number(player.form).toFixed(1)}</td><td>{Number(player.selected_by_percent).toFixed(1)}%</td><td><span className={player.status === "a" ? "availability ready" : "availability risk"}>{player.news || (player.status === "a" ? "Available" : "Flagged")}</span></td></tr>)}</tbody></table>{filtered.length === 0 ? <div className="empty-state"><h3>No matches</h3><p>Try a different player, club, position or availability filter.</p></div> : null}</div></section>;
}
