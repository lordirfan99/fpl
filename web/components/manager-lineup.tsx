"use client";

import { X } from "lucide-react";
import { Pitch } from "@/components/pitch";
import type { Bootstrap, Manager } from "@/lib/types";

export function ManagerLineup({ manager, bootstrap, gameweek, onClose }: { manager: Manager; bootstrap: Bootstrap; gameweek: number; onClose: () => void }) {
  const starters = manager.squad.slice(0, 11);
  const formation = ["DEF", "MID", "FWD"].map((position) => starters.filter((pick) => pick.position === position).length).join("-");
  return <div className="manager-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="manager-dialog" role="dialog" aria-modal="true" aria-labelledby="manager-dialog-title">
      <header><div><span>MANAGER DETAIL · GW{gameweek}</span><h2 id="manager-dialog-title">{manager.entry_name}</h2><p>{manager.player_name} · League rank #{manager.league_rank.toLocaleString()} · FPL ID {manager.entry_id}</p></div><button type="button" onClick={onClose} aria-label="Close lineup"><X size={18} /></button></header>
      <div className="manager-dialog-metrics"><article><span>GW points</span><strong>{manager.gw_points}</strong></article><article><span>Total</span><strong>{manager.total_points}</strong></article><article><span>Overall rank</span><strong>{manager.overall_rank.toLocaleString()}</strong></article><article><span>Squad value</span><strong>£{manager.squad_cost.toFixed(1)}m</strong></article></div>
      <div className="manager-lineup-heading"><div><span>STARTING XI</span><strong>{formation}</strong></div><small>11 on field · 4 substitutes · Captain: {manager.captain}</small></div>
      <Pitch squad={manager.squad} bootstrap={bootstrap} />
    </section>
  </div>;
}
