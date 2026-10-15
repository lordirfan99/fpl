import Link from "next/link";
import {getJournalIndexV2,getJournalEntryV2,gwParam} from "@/v2/public-data";
import {Heading,Card,Stat,DataState,Source} from "@/v2/ui";
type Params={gw?:string};
export default async function JournalV2({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams,index=await getJournalIndexV2(),gw=gwParam(p.gw);
 const entry=typeof gw==="number"?await getJournalEntryV2(gw):null;
 return <><Heading kicker="PERMANENT ARCHIVE" title="Season decision journal">Frozen evidence and outcomes for season 2026/27. Missing records remain missing; no synthetic results.</Heading>
 {!index.ok?<DataState result={index} path="/v2/journal"/>:
 <><div className="fplv2-grid">
  <Stat label="Verified journal weeks" value={index.data.gameweeks.length} note="of 38"/>
  <Stat label="Last recorded week" value={index.data.gameweeks.length?"GW"+Math.max(...index.data.gameweeks.map(x=>x.gameweek)):"—"}/>
  <Stat label="Season" value={index.data.season}/><Stat label="Evidence" value="Immutable" note="Manifest-checksummed"/>
 </div>
 <section className="fplv2-card"><h2>Recorded gameweeks</h2><div className="fplv2-week-links"><div>
 {Array.from({length:38},(_,i)=>i+1).map(n=>{
 const recorded=index.data.gameweeks.some(x=>x.gameweek===n);
 return recorded?<Link key={n} href={`/v2/journal?gw=${n}`}>GW{n} · archived</Link>:<span key={n} style={{padding:"7px 11px",opacity:.43}}>GW{n} · pending</span>;
 })}
 </div></div></section>
 <p><Link href="/journal">Existing detailed archive / CSV export →</Link></p></>}
 {gw==="invalid"&&<Card title="Invalid gameweek"><p>Select GW1–GW38 using the recorded timeline.</p></Card>}
 {entry&&!entry.ok&&<DataState result={entry} path="/v2/journal"/>}
 {entry?.ok&&<JournalData result={entry}/>}
 </>;
}
type Valid=Extract<Awaited<ReturnType<typeof getJournalEntryV2>>,{ok:true}>;
function JournalData({result}:{result:Valid}){
 const v=result.data;
 return <><Source capturedAt={result.capturedAt} stale={false} archived/>
 <div className="fplv2-grid"><Stat label="Gameweek" value={"GW"+v.gameweek}/><Stat label="GW points" value={v.summary?.gw_points??"—"}/><Stat label="Total points" value={v.summary?.total_points??"—"}/><Stat label="League rank" value={v.summary?.league_rank?"#"+v.summary.league_rank:"—"}/></div>
 <div className="fplv2-columns"><Card title="Verified outcome">
 <p>Captain: {v.summary?.captain||"Unavailable"} · Evidence quality: {v.quality?.status||"Unverified"}</p>
 {v.outcome?.squad?.length?<div className="fplv2-table-wrap"><table className="fplv2-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Player</th><th scope="col" role="columnheader">Role</th><th scope="col" role="columnheader">Points</th></tr></thead><tbody role="rowgroup">{v.outcome.squad.map(x=><tr role="row" key={x.element}><td role="cell" data-label="Player">{x.name}</td><td role="cell" data-label="Role">{x.is_captain?"Captain":x.multiplier?"XI":"Bench"}</td><td role="cell" data-label="Points">{x.points*x.multiplier}</td></tr>)}</tbody></table></div>:<p>No squad breakdown in this record.</p>}</Card>
 <Card title="Decision provenance"><p>{v.decision?.captured?"Deadline packet recorded":"No verified pre-deadline packet captured."}</p><p>Model: {v.decision?.model_version||"—"}</p><p>Record hash: <code>{v.record_hash.slice(0,20)}…</code></p>
 <h3>Public lessons</h3>{v.learning?.automated?.length?<ul>{v.learning.automated.map((lesson,i)=><li key={i}>{lesson}</li>)}</ul>:<p>No public lesson recorded.</p>}</Card></div></>;
}
