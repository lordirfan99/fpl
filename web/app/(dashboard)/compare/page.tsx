import { LeagueSwitcher, resolveLeague } from "@/components/league-switcher";
import { ManagerCompare } from "@/components/manager-compare";
import { PageHeader } from "@/components/page-header";
import { getCompactCatalog, getLeagueDirectory, getLeagueManager, MY_TEAM_ID } from "@/lib/data";

type Params = { league?: string; left?: string; right?: string; gw?: string };

export default async function ComparePage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const selected = resolveLeague(params.league);
  const requestedGw = params.gw && Number.isInteger(Number(params.gw)) && Number(params.gw) >= 1 && Number(params.gw) <= 38 ? Number(params.gw) : undefined;
  const directoryData = await getLeagueDirectory(selected.id, requestedGw);
  const directory = directoryData.managers;
  const defaultLeft = directory.find((manager) => manager.entry_id === MY_TEAM_ID)?.entry_id ?? directory[0]?.entry_id;
  const leftId = directory.some((row) => row.entry_id === Number(params.left)) ? Number(params.left) : defaultLeft;
  const rightId = directory.some((row) => row.entry_id === Number(params.right) && row.entry_id !== leftId) ? Number(params.right) : directory.find((row) => row.entry_id !== leftId)?.entry_id;
  if (!leftId || !rightId) return <div className="page-stack"><PageHeader eyebrow="HEAD TO HEAD" title="Compare managers" description="A finalized gameweek needs at least two managers to compare." /><LeagueSwitcher selected={selected.id} pathname="/compare" gameweek={requestedGw} /><section className="surface"><h2>No comparison available yet</h2><p>Wait for at least two verified entries in the selected league snapshot.</p></section></div>;
  const [left, right, bootstrap] = await Promise.all([
    getLeagueManager(selected.id, directoryData.gameweek, leftId),
    getLeagueManager(selected.id, directoryData.gameweek, rightId),
    getCompactCatalog(),
  ]);
  return <div className="page-stack"><PageHeader eyebrow="HEAD TO HEAD" title="Compare managers" description={`GW${directoryData.gameweek} finalized · Search the compact manager directory; only the two selected squads are loaded.${requestedGw && requestedGw !== directoryData.gameweek ? ` GW${requestedGw} is not finalized.` : ""}`} /><LeagueSwitcher selected={selected.id} pathname="/compare" gameweek={requestedGw} /><section className="surface"><ManagerCompare requestedGameweek={requestedGw} directory={directory} left={left} right={right} bootstrap={bootstrap} gameweek={directoryData.gameweek} leagueId={selected.id} /></section></div>;
}
