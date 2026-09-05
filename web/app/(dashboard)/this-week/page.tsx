import Link from "next/link";
import "@/app/decision-room.css";
import { DecisionRoom } from "@/components/decision-room";
import { getPrivateDashboard } from "@/lib/private-dashboard";

export const dynamic = "force-dynamic";
export default async function ThisWeekPage() {
  const data = await getPrivateDashboard();
  return <div className="page-stack decision-home"><header><span className="evidence-label">YOUR DECISION ROOM</span><h1>This gameweek</h1><p>Your team. Your next move. The evidence behind it.</p></header>
    {data.status === "ready" && data.packet ? <DecisionRoom packet={data.packet} checkedAt={data.account_checked_at} /> : <section className="surface decision-unavailable"><h2>{data.status === "signed_out" ? "Your personal plan stays private" : "Plan unavailable"}</h2>
      <p>{data.status === "signed_out" ? "Sign in with your owner Google account to see your verified squad, bank and the same plan as Telegram." : "A current verified plan is not available. This does not mean you should hold your transfer. Check your latest Telegram plan and its input time."}</p>
      <Link href={data.status === "signed_out" ? "/sign-in" : "/league"}>{data.status === "signed_out" ? "Sign in with Google" : "Explore public league evidence"}</Link></section>}
  </div>;
}
