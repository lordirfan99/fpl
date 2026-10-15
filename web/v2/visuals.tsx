import type { Pick, Position } from "@/lib/types";

const positions: Position[] = ["GKP", "DEF", "MID", "FWD"];

function SquadPlayer({ pick }: { pick: Pick }) {
  return <div className="fplv2-pitch-player">
    <div className="fplv2-shirt">
      <svg viewBox="0 0 64 64" aria-hidden="true"><path d="m20 8-15 9 8 15 8-4v28h22V28l8 4 8-15-15-9-6 6H26Z" fill="currentColor" stroke="#d7fff1" strokeWidth="2"/><path d="M27 16v34m10-34v34" stroke="#0e6558" strokeWidth="5"/></svg>
      {(pick.is_captain || pick.is_vice_captain) && <b>{pick.is_captain ? "C" : "VC"}</b>}
    </div>
    <strong>{pick.name}</strong>
    <small>{pick.team} · {pick.position}</small>
    <span>£{Number(pick.cost).toFixed(1)}m · {pick.multiplier}×</span>
    {pick.is_captain && <span className="fplv2-sr-only">Captain</span>}
    {pick.is_vice_captain && <span className="fplv2-sr-only">Vice captain</span>}
  </div>;
}

export function SquadPitch({ squad }: { squad: Pick[] }) {
  // Preserve official pick order: multipliers can include substitutes during Bench Boost.
  const eleven = squad.slice(0, 11), bench = squad.slice(11);
  const formation = positions.slice(1).map(p => eleven.filter(x => x.position === p).length).join("–");
  return <figure className="fplv2-squad">
    <figcaption><strong>Recorded lineup</strong><span>Formation {formation} · C captain · VC vice captain</span></figcaption>
    <div className="fplv2-pitch" aria-label="Recorded starting eleven football pitch">
      {positions.map(position => <div className="fplv2-pitch-row" key={position}>
        {eleven.filter(p => p.position === position).map(p => <SquadPlayer key={p.element} pick={p}/>)}
      </div>)}
    </div>
    <div className="fplv2-bench"><h3>Bench · recorded order</h3><div>{bench.map(p => <SquadPlayer key={p.element} pick={p}/>)}</div></div>
    <p className="fplv2-visual-note">Official pick slots shown. Multipliers reflect captured scoring, including substitutions and chips.</p>
  </figure>;
}

export function ShareChart({ label, rows }: { label: string; rows: { label: string; value: number }[] }) {
  return <figure className="fplv2-chart" aria-label={label}>
    <figcaption>{label}</figcaption>
    <ul>{rows.map(row => <li key={row.label}>
      <div><strong>{row.label}</strong><span>{row.value.toFixed(1)}%</span></div>
      <div className="fplv2-bar" aria-hidden="true"><span style={{ width: `${Math.max(0, Math.min(100, row.value))}%` }}/></div>
    </li>)}</ul>
  </figure>;
}

export function Difficulty({ value }: { value: number }) {
  return <span className={`fplv2-fdr fplv2-fdr-${value}`}>{value}<span className="fplv2-sr-only"> out of 5 difficulty</span></span>;
}
