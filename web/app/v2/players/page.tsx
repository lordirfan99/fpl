import { getCatalogV2 } from "@/v2/public-data";
import { Heading, DataState, Source, Stat } from "@/v2/ui";
type Params={q?:string;position?:string;status?:string};
const positions:Record<number,string>={1:"Goalkeeper",2:"Defender",3:"Midfielder",4:"Forward"};
export default async function PlayersV2({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams,result=await getCatalogV2();
 return <><Heading kicker="PLAYER MARKET" title="Official recorded player pool">Search players by name or club, filter position and check captured availability and price.</Heading>
 {!result.ok ? <DataState result={result} path="/v2/players"/> : <PlayersData result={result} p={p}/>}
 </>;
}
type Valid=Extract<Awaited<ReturnType<typeof getCatalogV2>>,{ok:true}>;
function PlayersData({result,p}:{result:Valid;p:Params}){
 const q=(p.q??"").trim().toLowerCase().slice(0,80);
 const position=Number(p.position);const club=new Map(result.data.teams.map(t=>[t.id,t.short_name]));
 const candidates=result.data.players.filter(x=>(!q || `${x.web_name} ${club.get(x.team)||""}`.toLowerCase().includes(q)) &&
  (![1,2,3,4].includes(position)||x.element_type===position) &&
  (p.status!=="available"||x.status==="a"));
 const shown=[...candidates].sort((a,b)=>Number(b.now_cost)-Number(a.now_cost)).slice(0,100);
 return <><Source capturedAt={result.capturedAt} stale={result.stale}/>
  <div className="fplv2-grid"><Stat label="Players recorded" value={result.data.players.length.toLocaleString()}/><Stat label="Search matches" value={candidates.length.toLocaleString()}/><Stat label="Active player records" value={result.data.players.filter(x=>x.status==="a").length.toLocaleString()}/><Stat label="Teams" value={result.data.teams.length}/></div>
  <section className="fplv2-card"><h2>Player finder</h2>
   <form className="fplv2-toolbar" action="/v2/players" method="get">
    <input aria-label="Player search" name="q" placeholder="Player or club" defaultValue={p.q||""} type="search"/>
    <select aria-label="Position filter" name="position" defaultValue={p.position||""}><option value="">All positions</option>{Object.entries(positions).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
    <select aria-label="Availability filter" name="status" defaultValue={p.status||""}><option value="">All statuses</option><option value="available">Available</option></select>
    <button type="submit">Apply filters</button>
   </form>
   <p>Showing up to 100 of {candidates.length.toLocaleString()} matching players, sorted by recorded price.</p>
   <div className="fplv2-table-wrap"><table className="fplv2-table">
    <thead><tr><th>Player</th><th>Club</th><th>Position</th><th>Price</th><th>FPL form</th><th>Status</th></tr></thead>
    <tbody>{shown.map(x=><tr key={x.id}><td><strong>{x.web_name}</strong>{x.news&&<small style={{display:"block",opacity:.65}}>{x.news.slice(0,90)}</small>}</td><td>{club.get(x.team)||"—"}</td><td>{positions[x.element_type]||"—"}</td><td>£{(x.now_cost/10).toFixed(1)}m</td><td>{x.form||"—"}</td><td>{x.status==="a"?"Available":x.status||"Unverified"}</td></tr>)}</tbody>
   </table></div>
   {!candidates.length&&<p>No players matched your filters. Try another position or search.</p>}
  </section>
 </>;
}
