import Link from "next/link";
import { getCatalogV2,getFixturesV2,getLeagueV2,MY_TEAM } from "@/v2/public-data";
import {Heading,Card,Stat,DataState,Source} from "@/v2/ui";

export default async function PlannerV2(){
 const [catalog,fixtures,league]=await Promise.all([getCatalogV2(),getFixturesV2(),getLeagueV2(58005)]);
 const owner=league.ok?league.data.managers.find(m=>m.entry_id===MY_TEAM):null;
 const now=Date.now();
 const next=catalog.ok?catalog.data.events.filter(e=>e.deadline_time&&Date.parse(e.deadline_time)>now)
  .sort((a,b)=>Date.parse(a.deadline_time!)-Date.parse(b.deadline_time!))[0]:undefined;
 const from=next?.id??null, weeks=from?Array.from({length:Math.min(5,39-from)},(_,i)=>from+i):[];
 return <><Heading kicker="FIXTURE PLANNER" title="Five gameweeks ahead">Official fixture context for research only. Transfers require verified account state and explicit owner approval.</Heading>
 {!catalog.ok&&<DataState result={catalog} path="/v2/planner"/>}
 {!fixtures.ok&&<DataState result={fixtures} path="/v2/planner"/>}
 {!league.ok&&<DataState result={league} path="/v2/league" league={58005}/>}
 {league.ok&&<Source capturedAt={league.capturedAt} stale={league.stale}/>}
 <div className="fplv2-grid">
 <Stat label="Next deadline" value={from?"GW"+from:"Not announced"}/>
 <Stat label="Fixture horizon" value={weeks.length?weeks.length+" weeks":"Unavailable"}/>
 <Stat label="Recorded squad" value={owner?.squad.length??"—"} note="No verified transfers"/>
 <Stat label="Personal action" value="Locked" note="Requires private FPL account"/>
 </div>
 {fixtures.ok&&weeks.length>0?<section className="fplv2-card"><h2>Official upcoming fixtures</h2>
 {weeks.map(gw=><div key={gw} style={{padding:"10px 0",borderBottom:"1px solid #2b3c4e"}}>
 <h3 style={{fontSize:15}}>Gameweek {gw}</h3>
 {Array.isArray(fixtures.data.gameweeks[String(gw)])&&fixtures.data.gameweeks[String(gw)].length?
 <div className="fplv2-table-wrap"><table className="fplv2-table"><thead><tr><th>Home</th><th>Away</th><th>Home FDR</th><th>Away FDR</th></tr></thead><tbody>
 {fixtures.data.gameweeks[String(gw)].map((x,i)=><tr key={i}><td>{x.team_h}</td><td>{x.team_a}</td><td>{x.team_h_difficulty}</td><td>{x.team_a_difficulty}</td></tr>)}
 </tbody></table></div>:<p>Fixtures for GW{gw} have not been published.</p>}
 </div>)}</section>:<Card title="No upcoming fixture window"><p>There is no future deadline available in the recorded official season calendar. Do not infer a gameweek.</p></Card>}
 <Card title="Account verification required"><p>Public prices and fixtures cannot establish your actual selling prices, free transfers, chips, private budget or pending changes. This view intentionally avoids generating a supposedly actionable plan from incomplete account data.</p><div className="fplv2-actions"><Link href="/sign-in">Open existing verified personal plan</Link></div></Card>
 </>;
}