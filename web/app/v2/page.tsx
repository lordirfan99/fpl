import Link from "next/link";
import { getCatalogV2, getLeagueV2, MY_TEAM } from "@/v2/public-data";
import { Heading, Stat, Card, Source, DataState } from "@/v2/ui";
export default async function OverviewV2(){
 const [catalog,league] = await Promise.all([getCatalogV2(),getLeagueV2(58005)]);
 const me = league.ok ? league.data.managers.find(m=>m.entry_id===MY_TEAM) : undefined;
 const next= catalog.ok ? catalog.data.events.find(e=>e.is_next)
  ?? catalog.data.events.find(e=>e.is_current && !e.finished) : null;
 const date=next?.deadline_time && !Number.isNaN(Date.parse(next.deadline_time))
  ? new Date(next.deadline_time).toLocaleString("en-MY",{timeZone:"Asia/Kuala_Lumpur",dateStyle:"medium",timeStyle:"short"}) : "Not announced";
 return <>
  <Heading kicker="REBUILT / LIVE PUBLIC EVIDENCE" title="Your next FPL decision">Fast, source-aware research. Private transfers and captain advice are shown only after verified account access.</Heading>
  {league.ok ? <Source capturedAt={league.capturedAt} stale={league.stale}/> : <DataState result={league} path="/v2/league" league={58005}/>}
  <div className="fplv2-grid">
    <Stat label="Your league rank" value={me ? "#"+me.league_rank.toLocaleString() : "Unavailable"} note="KK Old Boys"/>
    <Stat label="Your GW score" value={me?.gw_points ?? "—"} note={league.ok ? "Captured GW"+league.data.gameweek : "No capture"}/>
    <Stat label="Your total points" value={me?.total_points ?? "—"}/>
    <Stat label="Next deadline" value={next?"GW"+next.id:"Unavailable"} note={date}/>
  </div>
  <div className="fplv2-link-cards">
    <Link href="/v2/team"><strong>My Team →</strong><span>All 15 recorded picks, captain and bench</span></Link>
    <Link href="/v2/league"><strong>League →</strong><span>Standings, managers and verified GWs</span></Link>
    <Link href="/v2/elite"><strong>Elite 5% →</strong><span>Formation, captaincy and squad consensus</span></Link>
    <Link href="/v2/players"><strong>Players →</strong><span>Current recorded prices and availability</span></Link>
    <Link href="/sign-in"><strong>Verified plan →</strong><span>Private account, explicit approval required</span></Link>
  </div>
  <Card title="Evidence boundary">
   <p>Public snapshots are collected periodically, not minute-by-minute. An unavailable Gameweek or incomplete source never becomes a made-up score or actionable transfer recommendation. The original verified private plan stays separate.</p>
  </Card>
 </>;
}