import { PlayerExplorer } from "@/components/player-explorer";
import { PageHeader } from "@/components/page-header";
import { getCompactCatalog } from "@/lib/data";
import { deriveSeasonContext } from "@/lib/season";
import { formatMYT } from "@/lib/format";

export default async function PlayersPage() {
  const catalog = await getCompactCatalog();
  const finalizedGw = catalog.events.filter(event => event.finished && event.data_checked).at(-1)?.id ?? 0;
  const season = deriveSeasonContext(catalog.events, { finalizedGw });
  const players = catalog.elements.map(({ id, web_name, team, element_type, now_cost, ep_next, form, selected_by_percent, status, news }) => ({ id, web_name, team, element_type, now_cost, ep_next, form, selected_by_percent, status, news }));
  const teams = catalog.teams.map(({ id, short_name }) => ({ id, short_name }));
  const capture = catalog.meta?.snapshot_at ? formatMYT(catalog.meta.snapshot_at) : null;
  return <div className="page-stack"><PageHeader eyebrow={`PLAYER INTELLIGENCE · TARGET GW${season.nextDeadlineGw}`} title="Player market" description="Search and filter the recorded FPL player pool before building a transfer scenario." /><p role="status">{capture ? `Catalog captured ${capture}.` : "Catalog capture time unavailable."}{catalog.meta?.stale !== false || catalog.meta?.quality_status !== "valid" ? " Freshness or source verification is incomplete; check current prices and team news before acting." : ""}</p><PlayerExplorer players={players} teams={teams} targetGameweek={season.nextDeadlineGw} /></div>;
}
