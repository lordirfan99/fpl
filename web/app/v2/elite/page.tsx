import { ShareChart } from "@/v2/visuals";
import { getLeagueV2, gwParam, leagueId, MY_TEAM } from "@/v2/public-data";
import { Heading, Stat, Source, DataState, LeagueNav, WeekNav } from "@/v2/ui";

type Params={league?:string;gw?:string};
export default async function EliteV2({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams,id=leagueId(p.league),gw=gwParam(p.gw),result=await getLeagueV2(id,gw);
 return <><Heading kicker="ELITE INTELLIGENCE" title="The top five percent">Measure actual squad ownership and captaincy in the recorded league, not guesses from a generic template.</Heading>
  <LeagueNav id={id} path="/v2/elite" gw={typeof gw==="number"?gw:null}/>
  {!result.ok ? <DataState result={result} path="/v2/elite" league={id}/> : <EliteData result={result} id={id} gw={typeof gw==="number"?gw:null}/>}
 </>;
}
type Valid=Extract<Awaited<ReturnType<typeof getLeagueV2>>,{ok:true}>;
function EliteData({result,id,gw}:{result:Valid;id:number;gw:number|null}){
 const population=result.data.managers;
 const elite=[...population].sort((a,b)=>(a.overall_rank||Number.MAX_SAFE_INTEGER)-(b.overall_rank||Number.MAX_SAFE_INTEGER)).slice(0,Math.ceil(population.length*.05));
 const picks=new Map<number,{name:string;position:string;team:string;owned:number;started:number;captained:number}>();
 for(const manager of elite)for(const pick of manager.squad){
  const entry=picks.get(pick.element)??{name:pick.name,position:pick.position,team:pick.team,owned:0,started:0,captained:0};
  entry.owned++;if(pick.multiplier>0)entry.started++;if(pick.is_captain)entry.captained++;picks.set(pick.element,entry);
 }
 const sorted=[...picks.values()].sort((a,b)=>b.owned-a.owned),captains=[...picks.values()].sort((a,b)=>b.captained-a.captained);
 const avg=elite.reduce((s,m)=>s+m.gw_points,0)/Math.max(1,elite.length);
 const you=population.find(m=>m.entry_id===MY_TEAM);
 return <>
  <WeekNav weeks={result.availableWeeks} id={id} path="/v2/elite" selected={gw}/>
  <p role="status">Verified {gw!==null?"finalized archive":"scheduled league capture"}: GW{result.data.gameweek}. Requested {gw!==null?"GW"+gw:"latest capture"}.</p>
  <Source capturedAt={result.capturedAt} stale={result.stale} archived={gw!==null}/>
  <div className="fplv2-grid"><Stat label="Elite managers" value={elite.length} note={"Top 5% of "+population.length.toLocaleString()}/>
   <Stat label="Average elite GW" value={avg.toFixed(1)}/><Stat label="Top captain" value={captains[0]?.name||"—"} note={captains[0]?`${Math.round(captains[0].captained/elite.length*100)}% captained`:undefined}/>
   <Stat label="Your GW gap" value={you?(you.gw_points-avg).toFixed(1):"—"} note={you?"Versus the elite mean":"Not in this league"}/>
  </div>
  <div className="fplv2-columns">
   <section className="fplv2-card"><h2>Elite squad consensus</h2><p>Every row uses the {elite.length} recorded elite managers as denominator. Captured squads can change after a deadline.</p>
    <ShareChart label="Most owned elite picks" rows={sorted.slice(0,5).map(x=>({label:x.name,value:x.owned/elite.length*100}))}/>
    <div className="fplv2-table-wrap"><table className="fplv2-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Player</th><th scope="col" role="columnheader">Role</th><th scope="col" role="columnheader">Owned</th><th scope="col" role="columnheader">Started</th></tr></thead><tbody role="rowgroup">{sorted.slice(0,25).map(row=><tr role="row" key={row.name+row.position}><td role="cell" data-label="Player">{row.name}</td><td role="cell" data-label="Role">{row.position}</td><td role="cell" data-label="Owned">{(row.owned/elite.length*100).toFixed(1)}%</td><td role="cell" data-label="Started">{(row.started/elite.length*100).toFixed(1)}%</td></tr>)}</tbody></table></div>
   </section>
   <section className="fplv2-card"><h2>Captaincy concentration</h2><ShareChart label="Elite captain share" rows={captains.filter(x=>x.captained>0).slice(0,5).map(x=>({label:x.name,value:x.captained/elite.length*100}))}/><p>Actual recorded captain flags, not predicted captain choices.</p>
    <div className="fplv2-table-wrap"><table className="fplv2-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Captain</th><th scope="col" role="columnheader">Managers</th><th scope="col" role="columnheader">Share</th></tr></thead><tbody role="rowgroup">{captains.filter(c=>c.captained>0).slice(0,20).map(c=><tr role="row" key={c.name}><td role="cell" data-label="Captain">{c.name}</td><td role="cell" data-label="Managers">{c.captained}</td><td role="cell" data-label="Share">{(c.captained/elite.length*100).toFixed(1)}%</td></tr>)}</tbody></table></div>
   </section>
  </div>
 </>;
}
