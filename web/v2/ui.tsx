import { Navigation } from "./navigation";
import Link from "next/link";
import type { ReactNode } from "react";
import type { DataResult } from "./public-data";
import { leagueName } from "./public-data";

export function Shell({ children }: { children: ReactNode }) {
  return <div className="fplv2-app">
    <a className="fplv2-skip" href="#v2-content">Skip to content</a>
    <header className="fplv2-header">
      <div className="fplv2-brand"><span className="fplv2-brand-icon">F</span><span><strong>FPL Scout</strong><small>Intelligence V2 · Preview</small></span></div>
      <Navigation/>
      <span className="fplv2-status">Public research · read only</span>
    </header>
    <main id="v2-content" className="fplv2-main">{children}</main>
    <footer className="fplv2-footer"><span>Verified FPL capture · not live scores</span><Link href="/this-week">Open existing decision room</Link></footer>
  </div>;
}
export function Heading({ kicker, title, children }: { kicker: string; title: string; children?: ReactNode }) {
  return <div className="fplv2-heading"><div><span className="fplv2-kicker">{kicker}</span><h1>{title}</h1>{children && <p>{children}</p>}</div><span className="fplv2-mark">V2 Preview</span></div>;
}
export function Card({ title, children }: { title: string; children: ReactNode }) {
  return <section className="fplv2-card"><h2>{title}</h2>{children}</section>;
}
export function Stat({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return <div className="fplv2-stat"><small>{label}</small><strong>{value}</strong>{note && <span>{note}</span>}</div>;
}
export function Source({ capturedAt, stale, archived }: { capturedAt: string | null; stale: boolean; archived?: boolean }) {
  const time = capturedAt && Number.isFinite(Date.parse(capturedAt))
    ? new Date(capturedAt).toLocaleString("en-MY", {timeZone:"Asia/Kuala_Lumpur",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:true}) : "Unknown capture";
  return <div className={stale ? "fplv2-source fplv2-warn" : "fplv2-source"}>
    <span>{archived ? "Frozen historical GW" : stale ? "Capture older than 12 hours" : "Scheduled official FPL snapshot"}</span>
    <strong>Captured {time} MYT</strong>
  </div>;
}
export function DataState<T>({ result, path, league }: { result: DataResult<T>; path: string; league?: number }) {
  if (result.ok) return null;
  return <section className="fplv2-card fplv2-empty" role="status">
    <span className="fplv2-kicker">DATA {result.code.toUpperCase()}</span>
    <h2>Recorded evidence is unavailable</h2>
    <p>{result.message} No results have been invented and no transfer recommendation is inferred.</p>
    {result.availableWeeks.length > 0 && <div className="fplv2-week-links"><strong>Verified archived gameweeks</strong><div>{result.availableWeeks.map(gw=><Link key={gw} href={`${path}?league=${league??58005}&gw=${gw}`}>GW{gw}</Link>)}</div></div>}
    <div className="fplv2-actions"><Link href={league ? `${path}?league=${league}` : path}>Try current capture</Link><Link href="/v2">Back to overview</Link></div>
  </section>;
}
export function LeagueNav({ id, path, gw }: { id:number; path:string; gw?: number|null }) {
  return <div className="fplv2-tabs" aria-label="Choose a tracked league">
    {[58005,131997].map(l=><Link key={l} aria-current={id===l?"page":undefined} className={id===l?"active":""} href={`${path}?league=${l}${gw ? `&gw=${gw}` : ""}`}>{leagueName(l)}</Link>)}
  </div>;
}
export function WeekNav({ weeks, id, path, selected }: { weeks:number[]; id:number; path:string; selected:number|null }) {
  return <div className="fplv2-week-links"><strong>Gameweek</strong><div><Link className={selected===null?"active":""} href={`${path}?league=${id}`}>Latest capture</Link>
    {weeks.map(gw=><Link key={gw} className={selected===gw?"active":""} href={`${path}?league=${id}&gw=${gw}`}>GW{gw}</Link>)}
  </div></div>;
}
