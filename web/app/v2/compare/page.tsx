import Link from "next/link";
import { getLeagueV2, gwParam, leagueId, MY_TEAM } from "@/v2/public-data";
import { Heading, DataState, LeagueNav, WeekNav, Source, Stat } from "@/v2/ui";

type Params={league?:string;gw?:string;left?:string;right?:string};
export default async function CompareV2({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams,id=leagueId(p.league),week=gwParam(p.gw),result=await getLeagueV2(id,week);
 return <><Heading kicker="HEAD TO HEAD" title="Compare recorded managers">Compare two actual squads from the same tracked league and gameweek, with no guessed ownership.</Heading>
 <LeagueNav id={id} path="/v2/compare" gw={typeof week==="number"?week:null}/>
 {!result.ok?<DataState result={result} path="/v2/compare" league={id}/>:<CompareData result={result} id={id} gw={typeof week==="number"?week:null} p={p}/>}
 </>;
}
type Valid=Extract<Awaited<ReturnType<typeof getLeagueV2>>,{ok:true}>;
function CompareData({result,id,gw,p}:{result:Valid;id:number;gw:number|null;p:Params}){
 const managers=result.data.managers;
 const defaultLeft=managers.find(m=>m.entry_id===MY_TEAM)??managers[0],left=managers.find(m=>m.entry_id===Number(p.left))??defaultLeft;
 const right=managers.find(m=>m.entry_id===Number(p.right)&&m.entry_id!==left?.entry_id)??managers.find(m=>m.entry_id!==left?.entry_id);
 if(!left||!right)return <section className="fplv2-card"><h2>Comparison unavailable</h2><p>At least two real manager records are needed.</p></section>;
 const leftIds=new Set(left.squad.map(x=>x.element)),rightIds=new Set(right.squad.map(x=>x.element));
 const onlyLeft=left.squad.filter(x=>!rightIds.has(x.element)),onlyRight=right.squad.filter(x=>!leftIds.has(x.element));
 return <>
 <WeekNav weeks={result.availableWeeks} id={id} path="/v2/compare" selected={gw}/>
 <Source capturedAt={result.capturedAt} stale={result.stale} archived={gw!==null}/>
 <section className="fplv2-card"><h2>Select two managers</h2>
 <form action="/v2/compare" method="get" className="fplv2-toolbar">
 <input type="hidden" name="league" value={id}/>{gw&&<input type="hidden" name="gw" value={gw}/>}
 <p>Enter official FPL manager IDs found in the League directory. Unknown or duplicate IDs are safely ignored.</p>
 <label>First FPL ID <input type="number" min="1" name="left" aria-label="First manager" defaultValue={left.entry_id}/></label>
 <label>Second FPL ID <input type="number" min="1" name="right" aria-label="Second manager" defaultValue={right.entry_id}/></label>
 <button type="submit">Compare</button></form>
 </section>
 <div className="fplv2-grid"><Stat label={left.entry_name+" points"} value={left.gw_points}/><Stat label={right.entry_name+" points"} value={right.gw_points}/><Stat label="Common picks" value={15-onlyLeft.length} note="Out of 15"/><Stat label={"GW"+result.data.gameweek+" rank gap"} value={Math.abs(left.league_rank-right.league_rank)}/></div>
 <div className="fplv2-columns">
 {[{manager:left,other:onlyLeft},{manager:right,other:onlyRight}].map(({manager,other})=>
 <section className="fplv2-card" key={manager.entry_id}><h2>{manager.entry_name}</h2><p>Rank #{manager.league_rank} · Captain {manager.captain} · {other.length} different picks</p>
 <div className="fplv2-table-wrap"><table className="fplv2-table"><thead><tr><th>Player</th><th>Position</th><th>Role</th></tr></thead><tbody>{manager.squad.map(x=><tr key={x.element}><td>{x.name}{other.some(y=>y.element===x.element)?" *":""}</td><td>{x.position}</td><td>{x.is_captain?"Captain":x.is_vice_captain?"Vice":x.multiplier===0?"Bench":"XI"}</td></tr>)}</tbody></table></div>
 <p>* Unique to this manager&apos;s recorded squad</p>
 </section>)}
 </div>
 <p><Link href="/v2/league">Open full league directory →</Link></p>
 </>;
}
