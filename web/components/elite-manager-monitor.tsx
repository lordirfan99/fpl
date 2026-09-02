"use client";

import { Search, Shirt, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ManagerLineup } from "@/components/manager-lineup";
import type { Bootstrap, Manager, Pick } from "@/lib/types";

function formation(manager: Manager) {
  const starters = manager.squad.slice(0, 11);
  return ["DEF", "MID", "FWD"].map((position) => starters.filter((pick) => pick.position === position).length).join("-");
}

export function EliteManagerMonitor({ managers, bootstrap, gameweek, templateIds }: { managers: Manager[]; bootstrap: Bootstrap; gameweek: number; templateIds: number[] }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Manager | null>(null);
  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setSelected(null); };
    document.addEventListener("keydown", close);
    document.body.classList.add("modal-open");
    return () => { document.removeEventListener("keydown", close); document.body.classList.remove("modal-open"); };
  }, [selected]);
  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return term ? managers.filter((manager) => `${manager.entry_name} ${manager.player_name} ${manager.entry_id}`.toLocaleLowerCase().includes(term)) : managers;
  }, [managers, query]);
  const template = useMemo(() => new Set(templateIds), [templateIds]);
  return <>
    <div className="table-toolbar elite-monitor-toolbar"><label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search elite manager, team or FPL ID" /></label><span><Users size={13} /> {filtered.length} of {managers.length} elite managers · select one to open the full squad</span></div>
    <div className="elite-manager-cards">{filtered.map((manager, index) => {
      const overlap = manager.squad.filter((pick) => template.has(pick.element)).length;
      return <button key={manager.entry_id} type="button" className="elite-manager-card" onClick={() => setSelected(manager)}>
        <span className="elite-rank">{index + 1}</span><div><strong>{manager.entry_name}</strong><small>{manager.player_name}</small></div><dl><div><dt>GW</dt><dd>{manager.gw_points}</dd></div><div><dt>OR</dt><dd>#{manager.overall_rank.toLocaleString()}</dd></div><div><dt>XI</dt><dd>{formation(manager)}</dd></div><div><dt>Template</dt><dd>{overlap}/15</dd></div></dl><span className="view-squad"><Shirt size={13} /> View squad</span>
      </button>;
    })}</div>
    {selected && <ManagerLineup manager={selected} bootstrap={bootstrap} gameweek={gameweek} onClose={() => setSelected(null)} />}
  </>;
}

export function EliteSquadMatrix({ managers }: { managers: Manager[] }) {
  const players = useMemo(() => {
    const map = new Map<number, { pick: Pick; count: number }>();
    managers.forEach((manager) => manager.squad.forEach((pick) => {
      const row = map.get(pick.element) ?? { pick, count: 0 };
      row.count += 1;
      map.set(pick.element, row);
    }));
    return [...map.values()].sort((a, b) => b.count - a.count).slice(0, 24);
  }, [managers]);
  const visibleManagers = managers.slice(0, 16);
  return <div className="elite-matrix-wrap"><table className="elite-matrix"><thead><tr><th>Player</th>{visibleManagers.map((manager) => <th key={manager.entry_id} title={manager.entry_name}>{manager.entry_name.slice(0, 9)}</th>)}<th>Owned</th></tr></thead><tbody>{players.map(({ pick, count }) => <tr key={pick.element}><td><strong>{pick.name}</strong><small>{pick.position} · {pick.team}</small></td>{visibleManagers.map((manager) => { const owned = manager.squad.find((candidate) => candidate.element === pick.element); return <td key={manager.entry_id} className={owned ? owned.is_captain ? "matrix-captain" : owned.is_vice_captain ? "matrix-vice" : "matrix-owned" : ""}>{owned ? owned.is_captain ? "C" : owned.is_vice_captain ? "V" : "✓" : ""}</td>; })}<td><b>{(count / managers.length * 100).toFixed(0)}%</b></td></tr>)}</tbody></table></div>;
}
