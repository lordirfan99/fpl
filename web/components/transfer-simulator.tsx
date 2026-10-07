"use client";

import { useMemo, useState } from "react";
import { displayNumber, money, type DecisionPacket } from "@/lib/decision-room";
import { simulate, simulationPlayers, type Move } from "@/lib/transfer-simulator";

export function TransferSimulator({ packet }: { packet: DecisionPacket }) {
  const [moves, setMoves] = useState<Move[]>([]);
  const result = useMemo(() => simulate(packet, moves), [packet, moves]);
  const players = useMemo(() => {
    const catalog = new Map(simulationPlayers(packet).map(p => [p.id, p]));
    for (const p of packet.players) catalog.set(p.id, p);
    return [...catalog.values()];
  }, [packet]);
  const owned = new Set(packet.account.picks.map(p => p.element));
  const name = (id: number) => players.find(p => p.id === id)?.name ?? "Unavailable";
  const update = (index: number, patch: Partial<Move>) => setMoves(rows => rows.map((row, i) => i === index ? { ...row, ...patch } : row));
  return <section className="surface transfer-simulator">
    <span className="evidence-label">Private research · captured model estimates</span>
    <h2>Test transfer scenarios</h2>
    <p>Compare a complete package with keeping your squad. Each gameweek selects the best legal XI and captain for both squads. Estimates assume those lineups are set; they do not predict automatic substitutions.</p>
    {!packet.simulation_players ? <p>The full projection catalog is not in this capture yet. Choices are limited to players included in this verified plan.</p> : null}
    {moves.map((move, index) => {
      const position = players.find(p => p.id === move.out)?.position;
      return <fieldset key={index}><legend>Transfer {index + 1}</legend>
        <label>Sell player {index + 1}<select aria-label={`Sell player ${index + 1}`} value={move.out || ""} onChange={e => update(index, { out: Number(e.target.value), in: 0 })}><option value="">Choose outgoing player</option>{packet.account.picks.filter(p => p.element === move.out || !moves.some(m => m.out === p.element)).map(p => <option key={p.element} value={p.element}>{name(p.element)} · selling {money(p.selling_price / 10)}</option>)}</select></label>
        <label>Buy player {index + 1}<select aria-label={`Buy player ${index + 1}`} value={move.in || ""} disabled={!position} onChange={e => update(index, { in: Number(e.target.value) })}><option value="">Choose replacement</option>{players.filter(p => p.position === position && !owned.has(p.id) && (p.id === move.in || !moves.some(m => m.in === p.id))).sort((a, b) => (b.xpts_by_gw?.[0] ?? -1) - (a.xpts_by_gw?.[0] ?? -1) || a.id - b.id).map(p => <option key={p.id} value={p.id}>{p.name} · {money(p.cost === null ? null : p.cost / 10)} · {displayNumber(p.xpts_by_gw?.[0])} pts</option>)}</select></label>
        <button type="button" onClick={() => setMoves(rows => rows.filter((_, i) => i !== index))}>Remove transfer {index + 1}</button>
      </fieldset>;
    })}
    <div className="simulator-actions"><button type="button" disabled={moves.length >= 15} onClick={() => setMoves(rows => [...rows, { out: 0, in: 0 }])}>Add transfer</button><button type="button" onClick={() => setMoves(packet.transfers.map(t => ({ out: t.element_out, in: t.element_in })))}>Try recommended package</button><button type="button" onClick={() => setMoves([])}>Reset to hold</button></div>
    <div aria-live="polite">
      {result.errors.length ? <p role="status">{result.errors.join(" ")}</p> : <>
        <dl className="decision-account"><div><dt>Scenario bank</dt><dd>{money(result.bank)}</dd></div><div><dt>Free transfers available</dt><dd>{result.free === null ? "Unlimited · active chip" : result.free}</dd></div><div><dt>Scenario hit</dt><dd>{result.hit} points</dd></div><div><dt>Net horizon gain</dt><dd>{displayNumber(result.net)} pts</dd></div></dl>
        <div className="decision-fixtures"><table><caption>Raw projected points · hit deducted once from total gain</caption><thead><tr><th>Gameweek</th><th>Keep squad</th><th>Scenario</th><th>Gross gain</th></tr></thead><tbody>{result.weeks.map(week => <tr key={week.gw}><th>GW{week.gw}</th><td>{displayNumber(week.hold.points)}</td><td>{displayNumber(week.draft.points)}</td><td>{displayNumber(week.gain)}</td></tr>)}</tbody></table></div>
        {result.weeks[0] ? <details><summary>Scenario XI, captain and bench for GW{packet.gameweek}</summary><p>Captain: {name(result.weeks[0].draft.captain)} · Vice: {name(result.weeks[0].draft.vice)}</p><p>Starting XI: {result.weeks[0].draft.starters.map(name).join(" · ")}</p><p>Bench: {result.weeks[0].draft.bench.map(name).join(" · ")}</p></details> : null}
      </>}
    </div>
    {packet.chip === "freehit" || packet.account.chips.some(c => c.name === "freehit" && c.status_for_entry === "active") ? <p>Free Hit lasts one gameweek. The returning permanent squad is not projected here.</p> : null}
    <p className="decision-caption">This uses captured prices and forecasts, with no new model runs while editing. Positive gain is an estimate, not an approval or the planner’s risk-adjusted utility. Approval stays in Telegram.</p>
  </section>;
}
