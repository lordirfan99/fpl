"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, useTransition } from "react";
import type { FormEvent } from "react";
import { ManagerLineup } from "@/components/manager-lineup";
import type { Bootstrap, Manager, ManagerSummary } from "@/lib/types";

type Props = {
  managers: ManagerSummary[];
  selected?: Manager;
  myTeamId: number;
  bootstrap: Bootstrap;
  gameweek: number;
  leagueId: number;
  page: number;
  pages: number;
  filteredTotal: number;
  query: string;
};

export function LeagueTable(props: Props) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [query, setQuery] = useState(props.query);
  // Same-page param changes (page, search, open manager) re-render on the
  // server. Run them through a transition so the current table stays visible
  // and interactive-looking while the next result streams in.
  const navigate = useCallback((href: string) => {
    startTransition(() => router.push(href, { scroll: false }));
  }, [router]);
  const destination = useCallback((page: number, manager?: number, search = props.query) => {
    const params = new URLSearchParams({ league: String(props.leagueId), page: String(page) });
    if (search.trim()) params.set("q", search.trim());
    if (manager) params.set("manager", String(manager));
    return `/league?${params}`;
  }, [props.leagueId, props.query]);
  const closeManager = useCallback(() => navigate(destination(props.page)), [destination, navigate, props.page]);
  useEffect(() => {
    if (!props.selected) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") closeManager(); };
    document.addEventListener("keydown", close);
    document.body.classList.add("modal-open");
    return () => { document.removeEventListener("keydown", close); document.body.classList.remove("modal-open"); };
  }, [closeManager, props.selected]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    navigate(destination(1, undefined, query));
  };

  return <>
    <form className="table-toolbar" onSubmit={submit}>
      <label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search manager, team or FPL ID" /></label>
      <button type="submit">Search</button>
      <span>{props.filteredTotal.toLocaleString()} managers · full squads load only when selected</span>
    </form>
    <div className={`data-table-wrap${pending ? " is-pending" : ""}`} aria-busy={pending || undefined}><table className="data-table"><thead><tr><th>Rank</th><th>Team</th><th>Manager</th><th>GW</th><th>Total</th><th>Overall</th><th>Captain</th><th>Value</th></tr></thead><tbody>{props.managers.map((entry) => <tr key={entry.entry_id} className={`manager-row ${entry.entry_id === props.myTeamId ? "my-row" : ""}`} tabIndex={0} aria-label={`Open ${entry.entry_name} lineup`} onClick={() => navigate(destination(props.page, entry.entry_id))} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); navigate(destination(props.page, entry.entry_id)); } }}><td>#{entry.league_rank}</td><td><strong>{entry.entry_name}</strong><small>Load lineup</small></td><td>{entry.player_name}</td><td>{entry.gw_points}</td><td>{entry.total_points}</td><td>{entry.overall_rank.toLocaleString()}</td><td>{entry.captain}</td><td>£{entry.squad_cost.toFixed(1)}m</td></tr>)}</tbody></table></div>
    <div className="table-pagination"><button disabled={props.page === 1 || pending} onClick={() => navigate(destination(props.page - 1))}>Previous</button><span>Page {props.page} of {props.pages}</span><button disabled={props.page === props.pages || pending} onClick={() => navigate(destination(props.page + 1))}>Next</button></div>
    {props.selected ? <ManagerLineup manager={props.selected} bootstrap={props.bootstrap} gameweek={props.gameweek} onClose={closeManager} /> : null}
  </>;
}
