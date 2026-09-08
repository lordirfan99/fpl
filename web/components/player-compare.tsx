"use client";

import { Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { FixtureHorizon } from "@/lib/types";
import {
  MAX_COMPARE, availabilityLabel, fixtureLoad, fixtureRun, metricLeaders,
  type ComparePlayer,
} from "@/lib/player-compare";

type Option = { id: number; name: string; team: string; position: string; epNext: number };

export function PlayerCompare({ rows, options, horizon, gameweeks }: {
  rows: ComparePlayer[]; options: Option[]; horizon: FixtureHorizon; gameweeks: number[];
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const chosen = new Set(rows.map((row) => row.id));

  const matches = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return [];
    return options
      .filter((option) => !chosen.has(option.id) && `${option.name} ${option.team}`.toLowerCase().includes(term))
      .sort((a, b) => b.epNext - a.epNext)
      .slice(0, 8);
  }, [options, query, chosen]);

  const setIds = (ids: number[]) => {
    router.push(ids.length ? `/players/compare?ids=${ids.join(",")}` : "/players/compare");
    setQuery("");
  };
  const add = (id: number) => { if (rows.length < MAX_COMPARE) setIds([...rows.map((r) => r.id), id]); };
  const remove = (id: number) => setIds(rows.filter((r) => r.id !== id).map((r) => r.id));

  const priceLeaders = metricLeaders(rows, (r) => r.price, "low");
  const epLeaders = metricLeaders(rows, (r) => r.epNext, "high");
  const formLeaders = metricLeaders(rows, (r) => r.form, "high");
  const runByPlayer = new Map(rows.map((row) => [row.id, fixtureRun(row, horizon, gameweeks)]));
  const loadLeaders = metricLeaders(rows, (r) => fixtureLoad(runByPlayer.get(r.id) ?? []), "low");

  const cell = (id: number, leaders: Set<number>, content: React.ReactNode) =>
    <td key={id} className={leaders.has(id) ? "compare-best" : undefined}>{content}</td>;

  return <section className="surface table-surface">
    <div className="section-heading"><div><span>PLAYER RESEARCH</span><h2>Compare up to {MAX_COMPARE} players</h2>
      <p>Next-GW xPts is the official FPL estimate for GW{gameweeks[0] ?? "—"}; fixture difficulty is schedule context, not a points forecast.</p></div>
      <span className="section-chip">{rows.length}/{MAX_COMPARE} selected</span></div>

    <div className="player-toolbar">
      <label><Search size={15} /><span className="sr-only">Search players to compare</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Add a player or club"
          disabled={rows.length >= MAX_COMPARE} /></label>
      {rows.map((row) => <button type="button" key={row.id} className="compare-chip" onClick={() => remove(row.id)}
        aria-label={`Remove ${row.name}`}>{row.name} <X size={12} /></button>)}
    </div>
    {matches.length ? <div className="compare-matches" role="listbox" aria-label="Player matches">
      {matches.map((option) => <button type="button" role="option" aria-selected={false} key={option.id} onClick={() => add(option.id)}>
        <strong>{option.name}</strong><small>{option.position} · {option.team} · {option.epNext.toFixed(1)} xPts</small></button>)}
    </div> : null}

    {rows.length < 2 ? <div className="empty-state"><h3>Pick at least two players</h3>
      <p>Search above to add players. The link updates as you go, so a comparison can be shared or bookmarked.</p></div>
      : <div className="data-table-wrap"><table className="data-table compare-table"><thead><tr><th>Metric</th>
        {rows.map((row) => <th key={row.id}>{row.name}<small> · {row.position} · {row.teamShort}</small></th>)}</tr></thead>
        <tbody>
          <tr><th>Price</th>{rows.map((row) => cell(row.id, priceLeaders, `£${row.price.toFixed(1)}m`))}</tr>
          <tr><th>Next GW xPts</th>{rows.map((row) => cell(row.id, epLeaders, <strong>{row.epNext.toFixed(1)}</strong>))}</tr>
          <tr><th>Form</th>{rows.map((row) => cell(row.id, formLeaders, row.form.toFixed(1)))}</tr>
          <tr><th>Ownership</th>{rows.map((row) => <td key={row.id}>{row.ownership.toFixed(1)}%</td>)}</tr>
          <tr><th>Availability</th>{rows.map((row) => <td key={row.id}>
            <span className={row.status === "a" ? "availability ready" : "availability risk"}>{availabilityLabel(row)}</span></td>)}</tr>
          {gameweeks.map((gameweek, index) => <tr key={gameweek}><th>GW{gameweek}</th>{rows.map((row) => {
            const week = runByPlayer.get(row.id)?.[index] ?? [];
            return <td key={row.id}>{week.length
              ? week.map((match) => <span key={match.label} className={`compare-fdr fdr-${match.fdr}`}>{match.label} <b>{match.fdr}</b></span>)
              : <span className="compare-fdr blank">No fixture</span>}</td>;
          })}</tr>)}
          <tr><th>Fixture load ({gameweeks.length} GW · lower is easier)</th>
            {rows.map((row) => cell(row.id, loadLeaders, <strong>{fixtureLoad(runByPlayer.get(row.id) ?? [])}</strong>))}</tr>
        </tbody></table></div>}
  </section>;
}
