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
   <Source capturedAt={result.capturedAt} stale={result.stale} archived={selectedGw!==null}/>
   <div className="fplv2-grid"><Stat label="League managers" value={data.managers.length.toLocaleString()}/><Stat label="Your league rank" value={owner?"#"+owner.league_rank:"—"}/><Stat label="Your GW points" value={owner?.gw_points??"—"}/><Stat label="Recorded Gameweek" value={"GW"+data.gameweek} note={data.provisional?"Scheduled capture":"Frozen archive"}/></div>
   <section className="fplv2-card"><h2>Manager directory</h2>
    <form className="fplv2-toolbar" action="/v2/league" method="get">
     <input type="hidden" name="league" value={id}/>{selectedGw&&<input type="hidden" name="gw" value={selectedGw}/>}
     <input aria-label="Search manager" name="q" type="search" placeholder="Manager / team / FPL ID" defaultValue={p.q??""}/>
     <button type="submit">Search</button><span>{filtered.length.toLocaleString()} matches</span>
    </form>
    <div className="fplv2-table-wrap"><table className="fplv2-table">
     <thead><tr><th>Rank</th><th>Team / manager</th><th>GW</th><th>Total</th><th>Captain</th><th>Value</th></tr></thead>
     <tbody>{shown.map(m=><tr key={m.entry_id}><td>#{m.league_rank}</td><td><Link href={url({manager:String(m.entry_id),page:String(page)})}>{m.entry_name}</Link><small style={{display:"block",opacity:.7}}>{m.player_name}</small></td><td>{m.gw_points}</td><td>{m.total_points}</td><td>{m.captain}</td><td>£{m.squad_cost.toFixed(1)}m</td></tr>)}</tbody>
    </table></div>
    {!shown.length&&<p>No recorded manager matches this search.</p>}
    <div className="fplv2-actions">{page>1&&<Link href={url({page:String(page-1),manager:null})}>← Previous</Link>}<span style={{padding:12}}>Page {page} of {Math.max(1,Math.ceil(filtered.length/50))}</span>{page<Math.ceil(filtered.length/50)&&<Link href={url({page:String(page+1),manager:null})}>Next →</Link>}</div>
   </section>
   {detail&&<section className="fplv2-card" id="manager" aria-label="Recorded manager lineup"><h2>{detail.entry_name} · Recorded GW{data.gameweek}</h2><p>Captain: {detail.captain} · Full squad ({detail.squad.length} players)</p><div className="fplv2-table-wrap"><table className="fplv2-table"><thead><tr><th>Player</th><th>Position</th><th>Team</th><th>Captain</th><th>Multiplier</th></tr></thead><tbody>{detail.squad.map(p=><tr key={p.element}><td>{p.name}</td><td>{p.position}</td><td>{p.team}</td><td>{p.is_captain?"C":p.is_vice_captain?"VC":"—"}</td><td>{p.multiplier}</td></tr>)}</tbody></table></div><div className="fplv2-actions"><Link href={url({manager:null,page:String(page)})}>Close lineup</Link></div></section>}
 </>;
}
