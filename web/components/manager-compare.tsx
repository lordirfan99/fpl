"use client";

import { ArrowRight, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ManagerLineup } from "@/components/manager-lineup";
import type { Bootstrap, Manager, ManagerSummary, Pick } from "@/lib/types";

export function ManagerCompare({ directory, left, right, bootstrap, gameweek, leagueId }: { directory: ManagerSummary[]; left: Manager; right: Manager; bootstrap: Bootstrap; gameweek: number; leagueId: number }) {
  const router = useRouter();
  const [open, setOpen] = useState<Manager | null>(null);
  useEffect(() => { if (!open) return; const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(null); }; document.addEventListener("keydown", close); document.body.classList.add("modal-open"); return () => { document.removeEventListener("keydown", close); document.body.classList.remove("modal-open"); }; }, [open]);
  const select = (side: "left" | "right", id: number) => {
    const params = new URLSearchParams({ league: String(leagueId), left: String(side === "left" ? id : left.entry_id), right: String(side === "right" ? id : right.entry_id) });
    router.push(`/compare?${params}`);
  };
  const leftMap = new Map(left.squad.map((pick) => [pick.element, pick])), rightMap = new Map(right.squad.map((pick) => [pick.element, pick]));
  const shared = left.squad.filter((pick) => rightMap.has(pick.element)), leftOnly = left.squad.filter((pick) => !rightMap.has(pick.element)), rightOnly = right.squad.filter((pick) => !leftMap.has(pick.element));
  return <><div className="compare-selectors"><ManagerPicker label="Manager A" managers={directory} selectedId={left.entry_id} onSelect={(id) => select("left", id)} /><span className="compare-vs">VS</span><ManagerPicker label="Manager B" managers={directory} selectedId={right.entry_id} onSelect={(id) => select("right", id)} /></div>
    <div className="compare-kpis"><article><span>Squad overlap</span><strong>{shared.length}/15</strong></article><article><span>GW gap</span><strong>{Math.abs(left.gw_points - right.gw_points)}</strong></article><article><span>Rank gap</span><strong>{Math.abs(left.overall_rank - right.overall_rank).toLocaleString()}</strong></article><article><span>Value gap</span><strong>£{Math.abs(left.squad_cost - right.squad_cost).toFixed(1)}m</strong></article></div>
    <div className="compare-squads"><SquadColumn manager={left} picks={leftOnly} onOpen={() => setOpen(left)} /><SquadColumn manager={right} picks={rightOnly} onOpen={() => setOpen(right)} /><section className="compare-shared"><span>SHARED CORE</span><h3>{shared.length} players</h3><div>{shared.map((pick) => <PlayerChip key={pick.element} pick={pick} />)}</div></section></div>
    {open ? <ManagerLineup manager={open} bootstrap={bootstrap} gameweek={gameweek} onClose={() => setOpen(null)} /> : null}
  </>;
}

function ManagerPicker({ label, managers, selectedId, onSelect }: { label: string; managers: ManagerSummary[]; selectedId: number; onSelect: (id: number) => void }) {
  const selected = managers.find((manager) => manager.entry_id === selectedId) ?? managers[0];
  const [query, setQuery] = useState("");
  const matches = useMemo(() => { const term = query.trim().toLowerCase(); if (!term) return managers.slice(0, 6); return managers.filter((manager) => `${manager.entry_name} ${manager.player_name} ${manager.entry_id} ${manager.league_rank}`.toLowerCase().includes(term)).slice(0, 8); }, [managers, query]);
  return <div className="manager-picker"><span>{label}</span><label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Find ${label.toLowerCase()}`} aria-label={`Find ${label.toLowerCase()}`} /></label><strong>#{selected.league_rank} · {selected.entry_name}</strong><div className="manager-picker-results" role="listbox" aria-label={`${label} matches`}>{matches.map((manager) => <button type="button" role="option" aria-selected={manager.entry_id === selectedId} key={manager.entry_id} className={manager.entry_id === selectedId ? "active" : ""} onClick={() => { onSelect(manager.entry_id); setQuery(""); }}><b>#{manager.league_rank}</b><span>{manager.entry_name}<small>{manager.player_name}</small></span></button>)}</div></div>;
}

function SquadColumn({ manager, picks, onOpen }: { manager: Manager; picks: Pick[]; onOpen: () => void }) { return <section className="compare-manager"><header><div><span>#{manager.league_rank}</span><h3>{manager.entry_name}</h3><small>{manager.player_name} · {manager.gw_points} pts</small></div><button type="button" onClick={onOpen}>Full squad <ArrowRight size={13} /></button></header><div>{picks.map((pick) => <PlayerChip key={pick.element} pick={pick} />)}</div></section>; }
function PlayerChip({ pick }: { pick: Pick }) { return <span className="compare-player-chip"><strong>{pick.name}</strong><small>{pick.position} · {pick.team}{pick.is_captain ? " · C" : pick.is_vice_captain ? " · V" : ""}</small></span>; }
