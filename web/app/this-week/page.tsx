import "@/app/decision-room.css";
import "@/app/league-decision.css";
import { ThisWeekStatic } from "@/components/this-week-static";

export default function ThisWeekPage() {
  return <div className="page-stack decision-home">
    <header><span className="evidence-label">YOUR DECISION ROOM</span><h1>This gameweek</h1><p>Your team. Your next move. Public evidence loads after the static shell.</p></header>
    <ThisWeekStatic />
  </div>;
}
