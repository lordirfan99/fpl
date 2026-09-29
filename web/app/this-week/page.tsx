import { AppShell } from "@/components/app-shell";
import "@/app/decision-room.css";
import "@/app/league-decision.css";
import { ThisWeekStatic } from "@/components/this-week-static";

export default function ThisWeekPage() {
  return <AppShell><div className="page-stack decision-home">
    <header><span className="evidence-label">YOUR DECISION ROOM</span><h1>This gameweek</h1><p>The whole decision on one page: transfers, captain, XI, bench, bank — plus the recorded league facts behind them.</p></header>
    <ThisWeekStatic />
  </div></AppShell>;
}
