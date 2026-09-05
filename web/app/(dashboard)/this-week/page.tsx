import Link from "next/link";
import "@/app/decision-room.css";
import "@/app/league-decision.css";
import { DecisionRoom } from "@/components/decision-room";
import { getPrivateDashboard } from "@/lib/private-dashboard";
import { getLeagueDecision } from "@/lib/league-decision";
import { GoalProgress, RivalExposure } from "@/components/league-decision";
import { resolveLeague, leagues } from "@/components/league-switcher";

export const dynamic = "force-dynamic";
export default async function ThisWeekPage({ searchParams }: { searchParams: Promise<{ league?: string }> }) {
  const selected = resolveLeague((await searchParams).league);
  const [data, league] = await Promise.all([getPrivateDashboard(), getLeagueDecision(selected.id)]);
  return <div className="page-stack decision-home"><header><span className="evidence-label">YOUR DECISION ROOM</span><h1>This gameweek</h1><p>Your team. Your next move. The evidence behind it.</p></header>
    <nav className="decision-league-switch" aria-label="Decision league">{leagues.map(l => <Link key={l.id} href={`/this-week?league=${l.id}`} aria-current={l.id === selected.id ? "page" : undefined}>{l.name}</Link>)}</nav>
    <GoalProgress context={league} name={selected.name} />
    {data.status === "ready" && data.packet ? <DecisionRoom packet={data.packet} checkedAt={data.account_checked_at} rivalCaptaincy={{ gameweek: league?.gameweek, counts: Object.fromEntries((league?.ownership.rows ?? []).map(p => [p.element, p.target_captain_pct])) }}><RivalExposure context={league} ownedIds={data.packet.account.picks.map(p => p.element)} /></DecisionRoom> : <section className="surface decision-unavailable"><h2>{data.status === "signed_out" ? "Your personal plan stays private" : "Plan unavailable"}</h2>
      <p>{data.status === "signed_out" ? "Sign in with your owner Google account to see your verified squad, bank and the same plan as Telegram." : "A current verified plan is not available. This does not mean you should hold your transfer. Check your latest Telegram plan and its input time."}</p>
      <Link href={data.status === "signed_out" ? "/sign-in" : "/league"}>{data.status === "signed_out" ? "Sign in with Google" : "Explore public league evidence"}</Link></section>}
    {data.status !== "ready" ? <RivalExposure context={league} /> : null}
  </div>;
}
