import { LeagueSwitcher, resolveLeague } from "@/components/league-switcher";
import { ManagerCompare } from "@/components/manager-compare";
import { PageHeader } from "@/components/page-header";
import { getCompactCatalog, getLeagueDirectory, getLeagueManager, MY_TEAM_ID } from "@/lib/data";

type Params = { league?: string; left?: string; right?: string };

export default async function ComparePage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const selected = resolveLeague(params.league);
  const directoryData = await getLeagueDirectory(selected.id);
  const directory = directoryData.managers;
  const defaultLeft = directory.find((manager) => manager.entry_id === MY_TEAM_ID)?.entry_id ?? directory[0]?.entry_id;
  const leftId = directory.some((row) => row.entry_id === Number(params.left)) ? Number(params.left) : defaultLeft;
  const rightId = directory.some((row) => row.entry_id === Number(params.right) && row.entry_id !== leftId) ? Number(params.right) : directory.find((row) => row.entry_id !== leftId)?.entry_id;
  if (!leftId || !rightId) throw new Error(`League ${selected.id} needs at least two managers for comparison`);
  const [left, right, bootstrap] = await Promise.all([
    getLeagueManager(selected.id, directoryData.gameweek, leftId),
    getLeagueManager(selected.id, directoryData.gameweek, rightId),
    getCompactCatalog(),
  ]);
  return <div className="page-stack"><PageHeader eyebrow="HEAD TO HEAD" title="Compare managers" description="Search the compact manager directory; only the two selected squads are loaded." /><LeagueSwitcher selected={selected.id} pathname="/compare" /><section className="surface"><ManagerCompare directory={directory} left={left} right={right} bootstrap={bootstrap} gameweek={directoryData.gameweek} leagueId={selected.id} /></section></div>;
}
