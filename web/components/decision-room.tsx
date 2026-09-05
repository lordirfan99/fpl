"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Clock3, ShieldCheck } from "lucide-react";
import { displayNumber, fixturesFor, money, numeric, weeklyPoints, type DecisionPacket, type EvidencePlayer } from "@/lib/decision-room";
import { formatMYT } from "@/lib/format";

export function ComparisonBars({ rows, unit, scaleMax = 1 }: { rows: { label: string; value: number | null; proposed?: boolean }[]; unit: string; scaleMax?: number }) {
  const values = rows.flatMap(r => numeric(r.value) ? [r.value] : []);
  const minimum = Math.min(0, ...values), maximum = Math.max(scaleMax, ...values), span = maximum - minimum;
  return <div className="decision-bars" aria-label={unit}>{rows.map(row => <div className="decision-bar" key={row.label}>
    <div><span>{row.label}</span><strong>{displayNumber(row.value)}{numeric(row.value) ? ` ${unit}` : ""}</strong></div>
    <div className="decision-track" aria-hidden="true"><i className={row.proposed ? "proposed" : ""} style={{ left: `${((Math.min(0, row.value ?? 0) - minimum) / span) * 100}%`, width: `${numeric(row.value) ? Math.abs(row.value) / span * 100 : 0}%` }} /></div>
  </div>)}</div>;
}

function Evidence({ player, packet }: { player: EvidencePlayer; packet: DecisionPacket }) {
  return <details className="player-evidence"><summary>Evidence for {player.name}</summary>
    <div><span className="evidence-label">Recorded facts · {formatMYT(packet.timestamps.reference)}</span>
      <dl className="evidence-grid"><div><dt>Season minutes</dt><dd>{displayNumber(player.facts.minutes, 0)}</dd></div>
        <div><dt>Starts</dt><dd>{displayNumber(player.facts.starts, 0)}</dd></div>
        <div><dt>Season xG / xA</dt><dd>{player.facts.expected_goals ?? "Unavailable"} / {player.facts.expected_assists ?? "Unavailable"}</dd></div>
        <div><dt>Defensive contributions</dt><dd>{displayNumber(player.facts.defensive_contribution, 0)}</dd></div></dl>
      <p>{player.facts.news || "No official news recorded in this capture; this is not a guarantee of availability."}</p>
      {player.facts.news_added ? <small>News published {formatMYT(player.facts.news_added)}</small> : null}
      <p><span className="evidence-label">Model estimate</span> {displayNumber(player.xpts)} points · {displayNumber(player.expected_minutes, 0)} minutes</p>
    </div></details>;
}

export function DecisionRoom({ packet, checkedAt }: { packet: DecisionPacket; checkedAt?: string }) {
  const [proposed, setProposed] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [valid, setValid] = useState(true);
  const [latestCheckedAt, setLatestCheckedAt] = useState(checkedAt);
  useEffect(() => {
    let active = true;
    const verify = async () => {
      if (Date.now() >= Date.parse(packet.deadline) || !latestCheckedAt || Date.now() - Date.parse(latestCheckedAt) > 20 * 60_000) {
        if (active) setValid(false);
      }
      try {
        const response = await fetch("/api/private/dashboard", { cache: "no-store", signal: AbortSignal.timeout(12000) });
        const latest = await response.json();
        if (active) {
          const freshCheck = Date.parse(latest.account_checked_at);
          const stillValid = response.ok && latest.status === "ready" && latest.packet?.plan_id === packet.plan_id
            && latest.packet?.account_fingerprint === packet.account_fingerprint && Number.isFinite(freshCheck)
            && Date.now() - freshCheck >= 0 && Date.now() - freshCheck <= 20 * 60_000 && Date.now() < Date.parse(packet.deadline);
          if (stillValid) setLatestCheckedAt(latest.account_checked_at);
          setValid(stillValid);
        }
      } catch { if (active) setValid(false); }
    };
    const timer = setInterval(verify, 60_000);
    const focus = () => { void verify(); };
    window.addEventListener("focus", focus);
    return () => { active = false; clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [packet.plan_id, packet.account_fingerprint, packet.deadline, latestCheckedAt]);
  if (!valid) return <section className="surface" role="status"><h2>Plan unavailable</h2><p>The account check expired or this plan changed. Reload to retrieve the latest verified plan. No hold recommendation is implied.</p><button onClick={() => window.location.reload()}>Reload verified plan</button></section>;

  const currentIds = packet.account.picks.filter(p => p.position <= 11).sort((a, b) => a.position - b.position).map(p => p.element);
  const currentCaptain = packet.account.picks.find(p => p.is_captain)?.element;
  const starters = proposed ? packet.starters : currentIds;
  const bench = proposed ? packet.bench : packet.account.picks.filter(p => p.position > 11).sort((a, b) => a.position - b.position).map(p => p.element);
  const captain = proposed ? packet.captain : currentCaptain;
  const owned = new Set(packet.account.picks.map(p => p.element));
  const proposedIds = new Set([...packet.starters, ...packet.bench]);
  const selected = packet.players.find(p => p.id === selectedId);
  const chip = packet.chip === "wildcard" || packet.chip === "freehit";
  const hitCost = packet.transfers.filter(t => t.hit).length * 4;
  const utilityRows = packet.horizon.rows;
  const utility = utilityRows.length && utilityRows.every(r => numeric(r.gain) && numeric(r.weight))
    ? utilityRows.reduce((sum, row) => sum + row.gain! * row.weight, 0) : null;
  const paid = packet.alternatives.best_paid_transfer;
  const barRows = [{ label: "Keep current team", value: 0 }, { label: "Recommended changes", value: utility, proposed: true }];
  // Delayed routes start in other weeks: do not plot unlike horizons together.
  const summary = packet.transfers.length ? packet.transfers.map(t => `${t.out_name} → ${t.in_name}`).join(" · ") : packet.action;
  const playerButton = (id: number) => {
    const player = packet.players.find(p => p.id === id);
    if (!player) return <span key={id}>Player unavailable</span>;
    const changed = proposed ? !owned.has(id) : !proposedIds.has(id);
    return <button key={id} className={`decision-player ${changed ? "changed" : ""}`} onClick={() => setSelectedId(id)} aria-pressed={selectedId === id}>
      <span className="player-shirt">{captain === id ? "C" : player.position}</span>
      <strong>{player.name}</strong><small>{changed ? proposed ? "IN · " : "OUT · " : ""}{displayNumber(player.xpts)} pts*</small>
    </button>;
  };

  return <div className="decision-room">
    <section className="decision-headline"><div><span className="evidence-label">Verified personal plan · GW{packet.gameweek}</span>
      <h2>{chip ? `${packet.chip === "wildcard" ? "Wildcard" : "Free Hit"}: compare your full squad` : summary}</h2>
      {chip ? <p>{packet.transfers.length} squad changes proposed. Your chip is already active; this page cannot activate it.</p> : null}
      <p>{packet.reason || "The planner did not provide an explanation; review the evidence before deciding."}</p>
      <div className="decision-cautions"><p><strong>Main drawback:</strong> projections depend on playing time and can be wrong.{hitCost > 0 ? ` This plan also costs ${hitCost} actual points.` : ""}</p>
        <p><strong>Recheck if:</strong> your squad, prices, budget or team news changes. Approval stays in Telegram.</p></div>
      <small>Plan {packet.plan_id} · {formatMYT(packet.generated_at)}</small></div>
      <div className="deadline-tile"><Clock3 size={20} /><span>Deadline · MYT</span><strong>{formatMYT(packet.deadline)}</strong></div>
    </section>
    <dl className="decision-account"><div><dt>Bank now</dt><dd>{money(packet.account.transfers.bank / 10)}</dd></div><div><dt>Bank after</dt><dd>{money(packet.bank_after)}</dd></div>
      <div><dt>Free transfers</dt><dd>{chip || packet.account.transfers.unlimited ? "Unlimited" : displayNumber(packet.free_transfers_before, 0)}</dd></div><div><dt>Hit cost</dt><dd>{hitCost} points</dd></div></dl>

    <div className="decision-columns"><section className="surface decision-squad"><div className="decision-section-title"><div><span className="evidence-label">Recorded team → proposed team</span><h2>Your actual team</h2></div></div>
      <div className="decision-toggle" aria-label="Squad view"><button aria-pressed={!proposed} onClick={() => setProposed(false)}>Current squad</button><button aria-pressed={proposed} onClick={() => setProposed(true)}>Proposed squad</button></div>
      <div className="decision-pitch" aria-label={proposed ? "Proposed starting eleven" : "Current starting eleven"}>
        {["GKP", "DEF", "MID", "FWD"].map(position => <div className="decision-pitch-row" key={position}>{starters.filter(id => packet.players.find(p => p.id === id)?.position === position).map(playerButton)}</div>)}
      </div><h3>Bench order</h3><div className="decision-bench">{bench.map(playerButton)}</div>
      <p className="decision-caption">*Points are model estimates, not recorded scores. Tap a player for evidence. IN / OUT marks proposed transfers.</p>
      {selected ? <div aria-live="polite"><h3>{selected.name}</h3><Evidence player={selected} packet={packet} /></div> : null}
    </section>

    <section className="surface"><span className="evidence-label">Model estimate · not guaranteed points</span><h2>Is changing worth it?</h2>
      <ComparisonBars rows={barRows} unit="utility gain" />
      <p className="decision-caption">Three-GW risk-adjusted lineup utility, weighted by the planner. This is not a raw points forecast; hit cost is shown separately.</p>
      {packet.transfers.map(t => <div className="decision-transfer" key={`${t.element_out}-${t.element_in}`}><strong>{t.out_name} <ArrowRight size={14} /> {t.in_name}</strong>
        <small>Actual selling price: {money((packet.account.picks.find(p => p.element === t.element_out)?.selling_price ?? NaN) / 10)} · {t.hit ? "4-point hit" : chip ? "Active chip" : "Free transfer"}</small></div>)}
      <details className="player-evidence"><summary>Other routes the planner considered</summary>
        {Object.entries(packet.alternatives).filter(([key, value]) => key !== "hold" && value).map(([key, route]) => <div key={key} className="decision-transfer"><strong>{key.replaceAll("_", " ")}</strong>
          <p>{route!.moves.map(m => `${m.out} → ${m.in}`).join(" · ") || "No route supplied"}</p>
          <small>Starts GW{route!.projection_starts_gw ?? "—"} · {displayNumber(route!.horizon_gain)} utility gain · Budget after: unavailable</small>
          {key === "best_paid_transfer" ? <small>Diagnostic only; not approval to take a hit. Net after hit: {displayNumber(paid?.net_after_hit)} utility.</small> : null}
        </div>)}<p>Delayed routes cover different weeks and are not directly comparable with acting now. These are research alternatives, not approved plans.</p>
      </details>
    </section></div>

    <section className="surface"><span className="evidence-label">Model estimates + recorded fixtures</span><h2>Choose your captain with evidence</h2><div className="decision-captains">
      {packet.captains.map(c => { const player = packet.players.find(p => p.id === c.id); return <article key={c.id} className={c.id === packet.captain ? "chosen" : ""}><span>{c.id === packet.captain ? "RECOMMENDED CAPTAIN" : "ALTERNATIVE"}</span><h3>{c.name}</h3>
        <ComparisonBars rows={[{ label: "Projected points before captain multiplier", value: c.xpts, proposed: c.id === packet.captain }]} unit="pts" scaleMax={Math.max(1, ...packet.captains.flatMap(c => numeric(c.xpts) ? [c.xpts] : []))} />
        <p>{displayNumber(c.expected_minutes, 0)} expected minutes</p><p>{player ? fixturesFor(packet, player, packet.gameweek).map(f => f.label).join(" + ") || "No scheduled fixture" : "Fixture unavailable"}</p><p>{c.reason}</p>
        {player ? <Evidence player={player} packet={packet} /> : null}</article>; })}
    </div><p className="decision-caption">Rankings come from the same VM plan. Rival ownership is context—not a reason to sacrifice projected points.</p></section>

    <section className="surface"><span className="evidence-label">Model estimate</span><h2>Look beyond this deadline</h2><div className="decision-weeks">
      {Array.from({ length: Math.min(3, 39 - packet.gameweek) }, (_, offset) => <article key={offset}><h3>GW{packet.gameweek + offset}</h3>
        <ComparisonBars rows={[{ label: "Current XI", value: weeklyPoints(packet, currentIds, currentCaptain, offset) }, { label: "Proposed XI", value: weeklyPoints(packet, packet.starters, packet.captain, offset), proposed: true }]} unit="pts" />
      </article>)}
    </div><p className="decision-caption">Same selected XI and captain held across weeks, before transfer hits and future changes. {packet.chip === "freehit" ? "Free Hit lasts one GW: later proposed-XI bars are hypothetical, not your returning squad." : "Not the optimizer’s multi-week transfer roadmap."}</p>
      <div className="decision-fixtures"><table><caption>Recorded fixtures for the proposed squad · FDR 1 easier → 5 harder</caption><thead><tr><th>Player</th>{Array.from({ length: Math.min(3, 39 - packet.gameweek) }, (_, i) => <th key={i}>GW{packet.gameweek + i}</th>)}</tr></thead>
        <tbody>{[...packet.starters, ...packet.bench].map(id => { const p = packet.players.find(p => p.id === id); return p ? <tr key={id}><th>{p.name}</th>{Array.from({ length: Math.min(3, 39 - packet.gameweek) }, (_, i) => <td key={i}>{fixturesFor(packet, p, packet.gameweek + i).map(f => <span className={`decision-fdr level-${f.fdr}`} key={f.label}>{f.label} · {f.fdr}</span>)}{fixturesFor(packet, p, packet.gameweek + i).length === 0 ? "No fixture" : null}</td>)}</tr> : null; })}</tbody></table></div>
    </section>
    <details className="surface decision-provenance"><summary><ShieldCheck size={17} /> Sources and verification</summary><p>Account checked {formatMYT(latestCheckedAt)} · plan account capture {formatMYT(packet.timestamps.account)}</p><p>League {formatMYT(packet.timestamps.league)} · players and fixtures {formatMYT(packet.timestamps.reference)}</p><p>Model {packet.model_version}. No transfers, chip changes or lineup writes are available from this dashboard.</p></details>
  </div>;
}
