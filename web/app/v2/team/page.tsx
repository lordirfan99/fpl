import { getLeagueV2, MY_TEAM } from "@/v2/public-data";
import { Heading, Source, DataState, Stat, Card } from "@/v2/ui";
import Link from "next/link";
export default async function MyTeamV2(){
 const result=await getLeagueV2(58005);
 return <><Heading kicker="RECORDED SQUAD" title="My FPL team">15 recorded players, captain and substitutes. This is a scheduled public capture—not an authenticated transfer plan.</Heading>
 {!result.ok ? <DataState result={result} path="/v2/team"/> : <TeamContent result={result}/>}
 </>;
}
type Valid=Extract<Awaited<ReturnType<typeof getLeagueV2>>,{ok:true}>;
function TeamContent({result}:{result:Valid}){
 const manager=result.data.managers.find(m=>m.entry_id===MY_TEAM);
 if(!manager)return <Card title="Your team was not in this captured league"><p>Official FPL team {MY_TEAM} is not present. No squad has been inferred from another manager.</p></Card>;
 const starting=manager.squad.filter(p=>p.multiplier>0),bench=manager.squad.filter(p=>p.multiplier===0);
 return <><Source capturedAt={result.capturedAt} stale={result.stale}/>
 <div className="fplv2-grid"><Stat label="Team" value={manager.entry_name}/><Stat label="League rank" value={"#"+manager.league_rank}/><Stat label="GW points" value={manager.gw_points}/><Stat label="Overall rank" value={manager.overall_rank.toLocaleString()}/></div>
 <section className="fplv2-card"><h2>All 15 official recorded picks · GW{result.data.gameweek}</h2><p>Captain: {manager.captain} · {starting.length} scoring starters · {bench.length} substitutes. Active chip may change scoring multipliers.</p>
 <div className="fplv2-table-wrap"><table className="fplv2-table"><thead><tr><th>Player</th><th>Role</th><th>Club</th><th>Multiplier</th><th>Price</th></tr></thead><tbody>{manager.squad.map(p=><tr key={p.element}><td>{p.name}{p.is_captain?" (C)":p.is_vice_captain?" (VC)":""}</td><td>{p.position}</td><td>{p.team}</td><td>{p.multiplier}x</td><td>£{Number(p.cost).toFixed(1)}m</td></tr>)}</tbody></table></div></section>
 <Card title="Private plan remains protected"><p>This screen only reviews the public recorded team. Your real available bank, free transfers, current selling prices, captain plan and bot approvals require a separately verified private account packet.</p><div className="fplv2-actions"><Link href="/sign-in">Open verified private plan</Link></div></Card>
 </>;
}
