import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { PlayerCompare } from "@/components/player-compare";
import { getFixtureHorizon, getFullCatalog } from "@/lib/data";
import { POSITION_NAME, parseNumber, resolveCompareIds } from "@/lib/player-compare";
import { deriveSeasonContext } from "@/lib/season";

export const dynamic = "force-dynamic";

export default async function PlayerComparePage({ searchParams }: { searchParams: Promise<{ ids?: string }> }) {
  const { ids } = await searchParams;
  const catalogue = await getFullCatalog();
  const finalizedGw = catalogue.events.filter((event) => event.finished).map((event) => event.id).pop() ?? 1;
  const season = deriveSeasonContext(catalogue.events, { finalizedGw });
  const fromGw = season.nextDeadlineGw;
  const toGw = Math.min(fromGw + 4, 38);
  const gameweeks = Array.from({ length: toGw - fromGw + 1 }, (_, index) => fromGw + index);
  const horizon = await getFixtureHorizon(fromGw, toGw);

  const rows = resolveCompareIds(ids, catalogue);
  const teamById = new Map(catalogue.teams.map((team) => [team.id, team.short_name]));
  const options = catalogue.elements.map((player) => ({
    id: player.id, name: player.web_name, team: teamById.get(player.team) ?? "—",
    position: POSITION_NAME[player.element_type] ?? "—", epNext: parseNumber(player.ep_next),
  }));

  return <div className="page-stack">
    <PageHeader eyebrow={`PLAYER INTELLIGENCE · TARGET GW${fromGw}`} title="Compare players"
      description="Side-by-side xPts, form, price, ownership, availability and the next five fixtures. Research, not a personal recommendation." />
    <p className="compare-back"><Link href="/players">← Back to the player market</Link></p>
    <PlayerCompare rows={rows} options={options} horizon={horizon} gameweeks={gameweeks} />
  </div>;
}
