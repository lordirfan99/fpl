"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { TransferSimulator } from "./transfer-simulator";
import { DecisionRoom } from "./decision-room";
import type { DecisionPacket } from "@/lib/decision-room";
import { money } from "@/lib/decision-room";
import { formatMYT } from "@/lib/format";

type Context = {
  gameweek?: number;
  status?: string;
  snapshot_at?: string;
  freshness?: { freshness_hours?: number; stale?: boolean };
  goal?: {
    available?: boolean;
    owner_rank?: number;
    manager_count?: number;
    cutoff_rank?: number;
    points_gap?: number;
    inside_target?: boolean;
  };
  ownership?: { sample_count?: number; population?: number; rows?: { element: number; name: string; league_pct?: number; target_pct?: number | null; target_captain_pct?: number | null }[] };
};

type PrivateResponse = { status?: string; packet?: DecisionPacket | null; account_checked_at?: string };

const leagues = [
  { id: 58005, name: "KK Old Boys" },
  { id: 131997, name: "Overall IFE" },
];

const chipLabel = (chip: string | null) => chip === "wildcard" ? "Wildcard active" : chip === "freehit" ? "Free Hit active" : chip === "3xc" ? "Triple captain active" : chip === "bboost" ? "Bench boost active" : "None";

function deadlineCountdown(deadline: string, now: number | null) {
  if (!now) return null;
  const remaining = Date.parse(deadline) - now;
  if (!Number.isFinite(remaining)) return null;
  if (remaining <= 0) return "Deadline passed";
  const hours = Math.floor(remaining / 3_600_000);
  if (hours < 1) return `in ${Math.max(1, Math.floor(remaining / 60_000))} min`;
  if (hours < 48) return `in ${hours}h`;
  return `in ${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function ChecklistRow({ label, value, note }: { label: string; value: string; note?: string }) {
  return <li><span>{label}</span><strong>{value}</strong>{note ? <small>{note}</small> : null}</li>;
}

export function ThisWeekStatic() {
  const [leagueId, setLeagueId] = useState(58005);
  const [context, setContext] = useState<Context | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [retry, setRetry] = useState(0);
  const autoRetried = useRef(false);
  const [privatePlan, setPrivatePlan] = useState<PrivateResponse | null>(null);
  const [planValid, setPlanValid] = useState(true);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("league");
    const selected = Number(raw);
    if (leagues.some(league => league.id === selected)) setLeagueId(selected);
  }, []);

  // Sections arrive independently; a slow private check cannot delay league evidence.
  useEffect(() => {
    const controller = new AbortController();
    let active = true, receivedLeague = false, receivedPrivate = false;
    const timer = window.setTimeout(() => controller.abort(), 25000);
    setState("loading");
    setContext(null);
    const consume = (line: string) => {
      if (!active || !line.trim()) return;
      const item = JSON.parse(line);
      if (item.section === "league") {
        receivedLeague = true;
        setContext(item.data);
        setState(item.data ? "ready" : "unavailable");
      } else if (item.section === "private") {
        receivedPrivate = true;
        setPrivatePlan(item.data);
      }
    };
    const load = async () => {
      try {
        const response = await fetch(`/api/week?league=${leagueId}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok || !response.body) throw new Error("unavailable");
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let pending = "";
        while (true) {
          const chunk = await reader.read();
          pending += decoder.decode(chunk.value, { stream: !chunk.done });
          const lines = pending.split("\n");
          pending = lines.pop() ?? "";
          lines.forEach(consume);
          if (chunk.done) break;
        }
        if (pending.trim()) consume(pending);
      } catch { /* Keep any section already received. */ }
      finally {
        window.clearTimeout(timer);
        if (active) {
          if (!receivedPrivate) setPrivatePlan({ status: "unavailable" });
          if (!receivedLeague) setState("unavailable");
          if (!receivedLeague && !autoRetried.current) {
            autoRetried.current = true;
            setRetry(value => value + 1);
          }
        }
      }
    };
    void load();
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [leagueId, retry]);

  useEffect(() => {
    setNow(Date.now());
    const ticker = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(ticker);
  }, []);

  const selected = leagues.find(league => league.id === leagueId) ?? leagues[0];
  const goal = context?.goal;
  const complete = goal?.available && Number.isInteger(goal.owner_rank) && Number.isInteger(goal.cutoff_rank);
  const packet = privatePlan?.status === "ready" ? privatePlan.packet : null;
  const player = (id: number) => packet?.players.find(candidate => candidate.id === id);
  const moves = packet?.transfers.map(transfer => `${transfer.out_name} → ${transfer.in_name}`).join(" · ");
  const countdown = packet ? deadlineCountdown(packet.deadline, now) : null;
  const stale = context?.freshness?.stale ?? context?.status === "historical";

  return <>
    <nav className="decision-league-switch" aria-label="Decision league">{leagues.map(league => <a key={league.id} href={`/this-week?league=${league.id}`} aria-current={league.id === selected.id ? "page" : undefined}>{league.name}</a>)}</nav>

    {state === "loading" ? <section className="surface decision-unavailable" aria-busy="true" role="status"><h2 className="sr-only">Loading {selected.name} evidence</h2><div className="gw-skeleton" aria-hidden="true"><i className="bar title"></i><i className="bar"></i><i className="bar short"></i><i className="bar grid"></i></div><p>Loading recorded league evidence.</p></section> : null}
    {state === "unavailable" ? <section className="surface decision-unavailable"><h2>League evidence temporarily unavailable</h2><p>The recorded standings could not be loaded. Try again shortly.</p><button type="button" onClick={() => { autoRetried.current = false; setRetry(value => value + 1); }}>Retry</button></section> : null}

    {state === "ready" && context ? <section className="surface goal-progress">
      <div className="goal-heading">
        <div>
          <span className="evidence-label">RECORDED FACT · {selected.name}</span>
          <h2>{complete ? `${Math.abs(goal!.points_gap ?? 0)} points ${goal!.points_gap === 0 ? "level with" : goal!.points_gap! > 0 ? "to" : "ahead of"} the top-10% cutoff` : "Target progress unavailable"}</h2>
          <p>{complete ? `Rank ${goal!.owner_rank!.toLocaleString()} / ${goal!.manager_count!.toLocaleString()} · Target rank ${goal!.cutoff_rank} or better` : "Complete standings are required before calculating a cutoff."}</p>
        </div>
        <span className={`freshness-chip${stale ? " stale" : ""}`}>{stale ? "Historical capture" : (context.snapshot_at && Number.isFinite(Date.parse(context.snapshot_at)) ? `Captured ${formatMYT(context.snapshot_at)}` : "Capture time unavailable")}</span>
      </div>
      <p className="decision-caption">GW{context.gameweek ?? "?"} · {context.status === "historical" ? "Older capture: not current standings." : "Live recorded snapshot."} The cutoff moves as your rivals score.</p>
    </section> : null}

    {state === "ready" && context?.ownership?.rows?.length ? <section className="surface rival-exposure"><h2>Recorded rival picks</h2><ul>{context.ownership.rows.slice(0, 8).map(player => <li key={player.element}><span>{player.name}</span> · target group {player.target_pct ?? "—"}% · captaincy {player.target_captain_pct ?? "—"}%</li>)}</ul></section> : null}

    {packet && planValid ? <section className="surface gw-checklist" aria-label="Gameweek checklist">
      <div className="gw-checklist-head">
        <div>
          <span className="evidence-label">VERIFIED PLAN · GW{packet.gameweek}</span>
          <h2>{packet.chip === "wildcard" || packet.chip === "freehit" ? `${chipLabel(packet.chip)} — full squad comparison` : moves ?? packet.action}</h2>
          <p>{packet.reason || "The planner did not provide an explanation; review the evidence below before deciding."}</p>
        </div>
        <div className="gw-deadline"><span>Deadline</span><strong>{countdown ?? formatMYT(packet.deadline) ?? "Unavailable"}</strong><small>{formatMYT(packet.deadline) ?? ""}</small></div>
      </div>
      <ol className="gw-checklist-rows">
        <ChecklistRow label="Transfers" value={moves ?? packet.action} note={packet.transfers.length ? (packet.transfers.some(transfer => transfer.hit) ? "Includes a 4-point hit" : "Free transfer") : undefined} />
        <ChecklistRow label="Captain" value={player(packet.captain)?.name ?? "Unavailable"} note={player(packet.vice)?.name ? `VC: ${player(packet.vice)!.name}` : undefined} />
        <ChecklistRow label="Starting XI" value={`${packet.starters.length} set · bench ${packet.bench.length}`} />
        <ChecklistRow label="Chip" value={chipLabel(packet.chip)} />
        <ChecklistRow label="Bank after" value={money(packet.bank_after)} note={packet.free_transfers_before === null ? undefined : `${packet.free_transfers_before} free transfer${packet.free_transfers_before === 1 ? "" : "s"}`} />
        <ChecklistRow label="Next 3 GWs" value={packet.horizon.rows.map(row => `GW${row.gw}`).join(" · ")} note="Horizon estimates below" />
      </ol>
    </section> : null}
    {packet ? <DecisionRoom key={packet.plan_id + packet.account_fingerprint} onValidityChange={setPlanValid} packet={packet} checkedAt={privatePlan!.account_checked_at} rivalCaptaincy={{ gameweek: context?.gameweek, counts: Object.fromEntries((context?.ownership?.rows ?? []).map(player => [player.element, player.target_captain_pct ?? null])) }}><TransferSimulator key={packet.plan_id + packet.account_fingerprint} packet={packet} /></DecisionRoom> : null}

    {!packet && privatePlan?.status === "signed_out" ? <section className="surface gw-checklist locked"><h2>Your personal plan stays private</h2><p>One password unlocks the verified checklist: transfers, captain, XI, bench and bank.</p><ol className="gw-checklist-rows">
      <ChecklistRow label="Transfers" value="Locked" />
      <ChecklistRow label="Captain" value="Locked" />
      <ChecklistRow label="Starting XI" value="Locked" />
      <ChecklistRow label="Bank" value="Locked" />
    </ol><Link href="/sign-in">Unlock private plan</Link></section> : null}
    {!packet && privatePlan?.status === "unavailable" ? <section className="surface"><h2>Plan unavailable</h2><p>The verified plan is not available right now. This does not imply a hold recommendation.</p></section> : null}
    {!packet && !privatePlan ? <section className="surface"><h2>Your private plan</h2><p>The verified squad, bank and transfer plan loads privately after the static page is ready.</p><Link href="/sign-in">Unlock private plan</Link></section> : null}
  </>;
}
