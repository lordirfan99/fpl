"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeftRight, BarChart3, Beaker, BookOpenText, Bot, LayoutDashboard, ListChecks, Menu, RefreshCcw, Settings, Shield, Trophy, Users, X } from "lucide-react";

type NavItem = { href: string; label: string; icon: typeof Bot };

// Five primary destinations carry the weekly decision flow; research and
// one-off tools stay one tap away behind More instead of diluting the rail.
const primary: NavItem[] = [
  { href: "/this-week", label: "This Week", icon: LayoutDashboard },
  { href: "/planner", label: "Plan", icon: ListChecks },
  { href: "/league", label: "League", icon: Users },
  { href: "/players", label: "Players", icon: BarChart3 },
  { href: "/v5-lab", label: "Models", icon: Beaker },
];

const research: NavItem[] = [
  { href: "/journal", label: "Review", icon: BookOpenText },
  { href: "/my-team", label: "Public team review", icon: LayoutDashboard },
  { href: "/assistant", label: "League assistant", icon: Bot },
  { href: "/transfers", label: "Transfers & Chips", icon: RefreshCcw },
  { href: "/elite", label: "Elite 5%", icon: Trophy },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/compare", label: "Compare", icon: ArrowLeftRight },
  { href: "/settings", label: "Settings", icon: Settings },
];

const mobilePrimary = primary.filter(item => item.href !== "/v5-lab");

type PlanningContext = { latestSnapshotGw?: number; requestedSnapshotGw?: number; snapshotStatus?: "exact" | "fallback_missing" | "fallback_provisional"; planningGw?: number; deadline?: string; status?: string; dataSource?: string; dataQuality?: string };

export function AppShell({ children, context }: { children: React.ReactNode; context?: PlanningContext }) {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  const [navigationReady, setNavigationReady] = useState(false);
  useEffect(() => setNavigationReady(true), []);
  const planningLabel = context?.planningGw ? `Planning GW${context.planningGw}` : "Season snapshots connected";
  const researchActive = research.some(item => item.href === pathname);
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <Link href="/this-week" className="brand" aria-label="Fantasy Scout home">
          <span className="brand-mark"><Shield size={20} strokeWidth={2.4} /></span>
          <span><strong>Fantasy Scout</strong><small>Control Centre</small></span>
        </Link>
        <nav aria-label="Primary navigation">
          <div className="nav-section">
            {primary.map(({ href, label, icon: Icon }) => (
              <Link key={href} href={href} className={pathname === href ? "nav-link active" : "nav-link"}><Icon size={18} /><span>{label}</span></Link>
            ))}
          </div>
          <details className="nav-more" open={researchActive}>
            <summary><span>More</span></summary>
            <div className="nav-section">
              {research.map(({ href, label, icon: Icon }) => (
                <Link key={href} href={href} className={pathname === href ? "nav-link active" : "nav-link"}><Icon size={18} /><span>{label}</span></Link>
              ))}
            </div>
          </details>
        </nav>
        <div className="sidebar-status"><span className={context?.status === "rejected" ? "status-dot warning" : "status-dot"} /><div><strong>{planningLabel}</strong><small>{context?.latestSnapshotGw ? `Latest team snapshot: GW${context.latestSnapshotGw}` : "Open Plan for the next deadline"}</small></div></div>
      </aside>
      <div className="app-content">
        <header className="mobile-header"><Link href="/this-week" className="brand"><span className="brand-mark"><Shield size={18} /></span><strong>Fantasy Scout</strong></Link><span className="live-chip"><span className={context?.status === "rejected" ? "status-dot warning" : "status-dot"} />GW{context?.planningGw ?? ""}</span></header>
        <div className="planning-context" role="status"><span>Decision target</span><strong>{planningLabel}</strong>{context?.dataSource ? <small>Data: {context.dataSource}{context.dataQuality === "partial" ? " · partial hydration" : ""}</small> : null}{context?.snapshotStatus && context.snapshotStatus !== "exact" ? <small>Requested GW{context.requestedSnapshotGw}; showing the finalized GW{context.latestSnapshotGw} archive because GW{context.requestedSnapshotGw} is {context.snapshotStatus === "fallback_provisional" ? "still provisional" : "not yet archived"}.</small> : context?.latestSnapshotGw && context.latestSnapshotGw !== context.planningGw ? <small>Team and league review data currently ends at GW{context.latestSnapshotGw}.</small> : <small>Open each section to see its source and capture time.</small>}<details className="week-rail"><summary>Season weeks</summary><div>{Array.from({ length: 38 }, (_, index) => index + 1).map((gw) => { const isArchived = gw <= (context?.latestSnapshotGw ?? 0); const isLive = gw === (context?.latestSnapshotGw ?? 0) + 1; const isPlanning = gw === context?.planningGw; const researchTab = ["/elite", "/league", "/analytics", "/transfers", "/compare"].includes(pathname); const href = researchTab ? `${pathname}?gw=${gw}` : isArchived ? `/journal/2026-27/gw/${gw}` : isLive ? "/my-team" : isPlanning ? "/assistant" : `/planner?gw=${gw}`; return <Link key={gw} href={href} onClick={(event) => { const details = event.currentTarget.closest("details"); if (details) details.open = false; }} className={isArchived ? "archived" : isLive ? "live" : isPlanning ? "planning" : ""}>{isArchived ? "✓ " : isLive ? "• " : isPlanning ? "→ " : ""}GW{gw}</Link>; })}</div></details></div>
        <main>{children}</main>
        <nav className="mobile-nav" aria-label="Mobile navigation">
          {mobilePrimary.map(({ href, label, icon: Icon }) => <Link key={href} href={href} className={pathname === href ? "active" : ""}><Icon size={19} /><span>{label}</span></Link>)}
          <button type="button" disabled={!navigationReady} className={moreOpen || researchActive ? "active" : ""} onClick={() => setMoreOpen((open) => !open)} aria-expanded={moreOpen} aria-controls="mobile-more-menu">{moreOpen ? <X size={19} /> : <Menu size={19} />}<span>More</span></button>
        </nav>
        {moreOpen || researchActive ? <div className="mobile-more-menu" id="mobile-more-menu"><div><strong>Research &amp; tools</strong><button type="button" aria-label="Close more navigation" onClick={() => setMoreOpen(false)}><X size={16} /></button></div>{[...research, ...primary.filter(item => item.href === "/v5-lab")].map(({ href, label, icon: Icon }) => <Link key={href} href={href} onClick={() => setMoreOpen(false)} className={pathname === href ? "active" : ""}><Icon size={17} /><span>{label}</span></Link>)}</div> : null}
      </div>
    </div>
  );
}
