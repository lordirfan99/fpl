"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Context = {
  gameweek?: number;
  status?: string;
  snapshot_at?: string;
  goal?: {
    available?: boolean;
    owner_rank?: number;
    manager_count?: number;
    cutoff_rank?: number;
    points_gap?: number;
    inside_target?: boolean;
  };
  ownership?: { sample_count?: number; population?: number };
};

const API = "https://sportmania.duckdns.org/fpl-scout-api";
const leagues = [
  { id: 58005, name: "KK Old Boys" },
  { id: 131997, name: "Overall IFE" },
];

export function ThisWeekStatic() {
  const [leagueId, setLeagueId] = useState(58005);
  const [context, setContext] = useState<Context | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("league");
    const selected = Number(raw);
    if (leagues.some(league => league.id === selected)) setLeagueId(selected);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    fetch(`${API}/v1/leagues/${leagueId}/decision-context`, { signal: controller.signal })
      .then(response => response.ok ? response.json() as Promise<Context> : Promise.reject(new Error("unavailable")))
      .then(value => { setContext(value); setState("ready"); })
      .catch(error => { if (error.name !== "AbortError") setState("unavailable"); });
    return () => controller.abort();
  }, [leagueId]);

  const selected = leagues.find(league => league.id === leagueId) ?? leagues[0];
  const goal = context?.goal;
  const complete = goal?.available && Number.isInteger(goal.owner_rank) && Number.isInteger(goal.cutoff_rank);
  return <>
    <nav className="decision-league-switch" aria-label="Decision league">{leagues.map(league => <Link key={league.id} href={`/this-week?league=${league.id}`} aria-current={league.id === selected.id ? "page" : undefined}>{league.name}</Link>)}</nav>
    {state === "loading" ? <section className="surface decision-unavailable" aria-busy="true"><h2>Loading {selected.name} evidence…</h2><p>This page is static-first. Live league data loads separately and cannot block the page.</p></section> : null}
    {state === "unavailable" ? <section className="surface decision-unavailable"><h2>League evidence temporarily unavailable</h2><p>The static page is ready. The live snapshot API did not respond in time.</p><button type="button" onClick={() => setLeagueId(id => id)}>Retry</button></section> : null}
    {state === "ready" && context ? <section className="surface goal-progress"><span className="evidence-label">RECORDED FACT · {selected.name}</span><h2>{complete ? `${Math.abs(goal!.points_gap ?? 0)} points ${goal!.points_gap && goal!.points_gap > 0 ? "to" : "ahead of"} the top-10% cutoff` : "Target progress unavailable"}</h2><p>{complete ? `Rank ${goal!.owner_rank!.toLocaleString()} / ${goal!.manager_count!.toLocaleString()} · Target rank ${goal!.cutoff_rank} or better` : "Complete standings are required before calculating a cutoff."}</p><p className="decision-caption">GW{context.gameweek ?? "?"} · {context.status === "historical" ? "Historical capture" : "Live recorded snapshot"}</p></section> : null}
    <section className="surface"><h2>Your private plan</h2><p>The verified squad, bank and transfer plan must never be baked into a static public page.</p><Link href="/sign-in">Unlock private plan</Link></section>
  </>;
}
