import { SquadPitch } from "@/v2/visuals";
import Link from "next/link";
import { getLeagueV2, gwParam, leagueId, MY_TEAM } from "@/v2/public-data";
import { Heading, Stat, Source, DataState, LeagueNav, WeekNav } from "@/v2/ui";

type Params = { league?:string; gw?:string; q?:string; page?:string; manager?:string };
export default async function LeagueV2({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams,id=leagueId(p.league),requested=gwParam(p.gw);
 const result=await getLeagueV2(id,requested);
 return <><Heading kicker="LEAGUE INTELLIGENCE" title="Complete standings">Search a manager and inspect all 15 official recorded squad picks. Unpublished weeks are explicitly marked unavailable.</Heading>
 <LeagueNav id={id} path="/v2/league" gw={typeof requested==="number"?requested:null}/>
 {!result.ok ? <DataState result={result} path="/v2/league" league={id}/> : <LeagueContent result={result} p={p} id={id} selectedGw={typeof requested==="number"?requested:null}/>}
 </>;
}
type ValidResult=Extract<Awaited<ReturnType<typeof getLeagueV2>>,{ok:true}>;
function LeagueContent({result,p,id,selectedGw}:{result:ValidResult;p:Params;id:number;selectedGw:number|null}){
 const data=result.data,q=(p.q??"").trim().slice(0,80).toLowerCase();
 const filtered=data.managers.filter(m=>!q||`${m.entry_name} ${m.player_name} ${m.entry_id}`.toLowerCase().includes(q));
 const page=Math.min(Math.max(Number.isInteger(Number(p.page))&&Number(p.page)>0?Number(p.page):1,1),Math.max(1,Math.ceil(filtered.length/50)));
 const shown=[...filtered].sort((a,b)=>a.league_rank-b.league_rank).slice((page-1)*50,page*50);
 const owner=data.managers.find(m=>m.entry_id===MY_TEAM);
 const detail=data.managers.find(m=>m.entry_id===Number(p.manager));
 const url=(changes:Record<string,string|null>)=>{
  const qp=new URLSearchParams({league:String(id)});if(selectedGw)qp.set("gw",String(selectedGw));
  if(q)qp.set("q",q);for(const [k,v] of Object.entries(changes))v===null?qp.delete(k):qp.set(k,v);
  return "/v2/league?"+qp;
 };
 return <>
   <WeekNav weeks={result.availableWeeks} id={id} path="/v2/league" selected={selectedGw}/>
   <p role="status">Verified {selectedGw!==null?"finalized archive":"scheduled league capture"}: GW{data.gameweek}. Requested {selectedGw!==null?"GW"+selectedGw:"latest capture"}.</p>
   <Source capturedAt={result.capturedAt} stale={result.stale} archived={selectedGw!==null}/>
   <div className="fplv2-grid"><Stat label="League managers" value={data.managers.length.toLocaleString()}/><Stat label="Your league rank" value={owner?"#"+owner.league_rank:"—"}/><Stat label="Your GW points" value={owner?.gw_points??"—"}/><Stat label="Recorded Gameweek" value={"GW"+data.gameweek} note={data.provisional?"Scheduled capture":"Frozen archive"}/></div>
   <section className="fplv2-card"><h2>Manager directory</h2>
    <form className="fplv2-toolbar" action="/v2/league" method="get">
     <input type="hidden" name="league" value={id}/>{selectedGw&&<input type="hidden" name="gw" value={selectedGw}/>}
     <input aria-label="Search manager" name="q" type="search" placeholder="Manager / team / FPL ID" defaultValue={p.q??""}/>
     <button type="submit">Search</button><span>{filtered.length.toLocaleString()} matches</span>
    </form>
    <div className="fplv2-table-wrap"><table className="fplv2-table" role="table">
     <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Rank</th><th scope="col" role="columnheader">Team / manager</th><th scope="col" role="columnheader">GW</th><th scope="col" role="columnheader">Total</th><th scope="col" role="columnheader">Captain</th><th scope="col" role="columnheader">Value</th></tr></thead>
     <tbody role="rowgroup">{shown.map(m=><tr role="row" key={m.entry_id}><td role="cell" data-label="Rank">#{m.league_rank}</td><td role="cell" data-label="Team / manager"><Link href={url({manager:String(m.entry_id),page:String(page)})}>{m.entry_name}</Link><small style={{display:"block",opacity:.7}}>{m.player_name}</small></td><td role="cell" data-label="GW">{m.gw_points}</td><td role="cell" data-label="Total">{m.total_points}</td><td role="cell" data-label="Captain">{m.captain}</td><td role="cell" data-label="Value">£{m.squad_cost.toFixed(1)}m</td></tr>)}</tbody>
    </table></div>
    {!shown.length&&<p>No recorded manager matches this search.</p>}
    <div className="fplv2-actions">{page>1&&<Link href={url({page:String(page-1),manager:null})}>← Previous</Link>}<span style={{padding:12}}>Page {page} of {Math.max(1,Math.ceil(filtered.length/50))}</span>{page<Math.ceil(filtered.length/50)&&<Link href={url({page:String(page+1),manager:null})}>Next →</Link>}</div>
   </section>
   {detail&&<section className="fplv2-card" id="manager" aria-label="Recorded manager lineup"><h2>{detail.entry_name} · Recorded GW{data.gameweek}</h2><SquadPitch squad={detail.squad}/><p>Captain: {detail.captain} · Full squad ({detail.squad.length} players)</p><div className="fplv2-table-wrap"><table className="fplv2-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Player</th><th scope="col" role="columnheader">Position</th><th scope="col" role="columnheader">Team</th><th scope="col" role="columnheader">Captain</th><th scope="col" role="columnheader">Multiplier</th></tr></thead><tbody role="rowgroup">{detail.squad.map(p=><tr role="row" key={p.element}><td role="cell" data-label="Player">{p.name}</td><td role="cell" data-label="Position">{p.position}</td><td role="cell" data-label="Team">{p.team}</td><td role="cell" data-label="Captain">{p.is_captain?"C":p.is_vice_captain?"VC":"—"}</td><td role="cell" data-label="Multiplier">{p.multiplier}</td></tr>)}</tbody></table></div><div className="fplv2-actions"><Link href={url({manager:null,page:String(page)})}>Close lineup</Link></div></section>}
 </>;
}
