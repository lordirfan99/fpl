import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { packet } from './decision-packet.mjs';
const files = new Map();
const now = new Date().toISOString();
const data = packet();
const catalog = { meta: { snapshot_at: now, quality_status: 'valid' },
  players: data.players.map(p => ({ id: p.id, web_name: p.name, team: p.club,
    element_type: p.position === 'GKP' ? 1 : p.position === 'DEF' ? 2 : p.position === 'MID' ? 3 : 4,
    now_cost: p.cost, ep_next: '5.1', form: '4.0', selected_by_percent: '10.0', status: 'a', news: '' })),
  teams: data.teams, events: [{ id: 3, finished: true, data_checked: true, is_current: true },
    { id: 4, is_next: true, finished: false, deadline_time: new Date(Date.now() + 86400000).toISOString() }] };
const picks = data.players.slice(0,15).map((p,i) => ({ element: p.id, name: p.name, team: 'Test Club',
  cost: p.cost/10, position: p.position, multiplier: i === 0 ? 2 : i < 11 ? 1 : 0, is_captain: i === 0, is_vice_captain: i === 1 }));
const managers = [2797967,101010].map((id,i) => ({ entry_id:id, entry_name:`Test FC ${id}`, player_name:`Manager ${id}`,
  gw_points:56, total_points:340, overall_rank:12000, league_rank:i+1, squad_cost:100.5, captain:'Test Player 1', transfers_made:0,
  squad:picks.map((p,i) => [i,p.multiplier,p.is_captain,p.is_vice_captain]) }));
function put(path,value) { files.set(path,JSON.stringify(value)); }
put('catalog.json',catalog);put('fixtures.json',{gameweeks:Object.fromEntries([4,5].map(gw=>[gw,data.fixtures.filter(x=>x.event===gw).map(x=>({...x,team_h:'Test Home',team_a:'Test Away'}))]))});
put('projections.json',{ meta:{snapshot_at:now,generated_at:now,quality_status:'valid'},gameweek:4,projection_version:'projection-v5.0-lab',
  players:data.players.map(p=>({element:p.id,name:p.name,team:'Test Club',position:p.position,xpts_mean:5,p10:2,p50:5,p90:8,
    p_return:.4,p_10_plus:.1,expected_minutes:{p_start:.8,p_bench_appearance:.1,expected_minutes:75,p_60_plus:.8},components:{},source:'test',quality_issues:[]})) });
for (const id of [58005,131997]) {
  const league = {encoding:'league-squads-v1',league_id:id,count:2,declared_count:2,gameweek:3,provisional:false,
    meta:{snapshot_at:now,generated_at:now,quality_status:'valid'},players:picks.map(({multiplier,is_captain,is_vice_captain,...p})=>p),managers};
  put(`leagues/${id}/gw/3.json`,league);put(`leagues/${id}/live.json`,{...league,provisional:true});
  put(`leagues/${id}/team.json`,{source:'official-fpl-scheduled-snapshot',status:'recorded',gameweek:3,fetched_at:now,
    provisional:false,active_chip:null,chips_played:[],entry:{id:2797967,entry_name:'Test FC 2797967',player_name:'Owner',overall_rank:12000,total_points:340},
    league:{id,name:'Test League',entry_rank:1,rank_count:2},points:56,fixtures:[],
    picks:picks.map((p,i)=>({...p,position:i+1,web_name:p.name,team:1,points:3,now_cost:50}))});
  put(`leagues/${id}/decision.json`,{schema_version:1,league_id:id,gameweek:3,status:'ready',snapshot_at:now,history:[],
    goal:{available:true,manager_count:2,cutoff_rank:1,owner_rank:1,points_gap:0,inside_target:true},ownership:{rows:[]}});
  put(`leagues/${id}/research.json`,{gameweek:3,meta:{snapshot_at:now},packet_status:'advisory',
    freshness:{snapshot_at:now,account_state_verified:false,account_gameweek:3},competitive:{model_version:'competitive-v4.0',phase:'MATCH'},transfers:[],captains:[]});
}
put('journal/2026-27/index.json',{schema_version:1,season:'2026-27',gameweeks:[{gameweek:3,status:'final',quality:{status:'valid',issues:[]},record_hash:'c'.repeat(64),summary:{gw_points:56,total_points:340,league_rank:1,overall_rank:12000,elite_average:56,points_vs_reference:0,transfers:0,hit_cost:0}}],totals:{completed:1,points:56}});
put('journal/2026-27/gw/3.json',{season:'2026-27',gameweek:3,record_hash:'c'.repeat(64),generated_at:now,summary:{gw_points:56,total_points:340,league_rank:1,overall_rank:12000,captain:picks[0].name},outcome:{squad:picks.map(p=>({...p,points:3}))}});
files.set('journal/2026-27/gameweeks.csv','season,gameweek\n');
const manifest={schema_version:1,release:createHash('sha256').update(JSON.stringify([...files])).digest('hex'),revision:'b'.repeat(40),generated_at:now,writes_enabled:false,
  gameweeks:{58005:[3],131997:[3]},files:Object.fromEntries([...files].map(([p,s])=>[p,createHash('sha256').update(s).digest('hex')]))};
let calls=[];
createServer((req,res)=>{
  res.setHeader('Content-Type','application/json');
  if(req.url==='/__test/calls'){res.end(JSON.stringify(calls));return}
  if(req.url==='/__test/reset'){calls=[];res.end('{}');return}
  calls.push(req.url);
  if(req.url==='/manifest.json'){res.end(JSON.stringify(manifest));return}
  const file=req.url.replace(`/releases/${manifest.release}/`,'');
  if(files.has(file)){res.end(files.get(file));return}
  res.statusCode=404;res.end('{}');
}).listen(4195,'127.0.0.1');
