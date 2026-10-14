import { test, expect } from "@playwright/test";
import { decodeCompactLeague, type CompactLeague } from "../../lib/compact-league";

function payload(): CompactLeague {
  return { encoding: "league-squads-v1", count: 2, declared_count: 2, gameweek: 6,
    meta: { snapshot_at: "2026-10-01T08:00:00Z" },
    players: [{ element: 1, name: "Recorded Player", team: "Recorded Club", cost: 5, position: "MID", selected_by: 10 },
      { element: 1, name: "Recorded Player", team: "Recorded Club", cost: 5.5, position: "MID", selected_by: null }],
    managers: [1, 2].map((id, index) => ({ entry_id: id, entry_name: `Team ${id}`, player_name: `Manager ${id}`,
      gw_points: 50, total_points: 300, overall_rank: 100, league_rank: id, squad_cost: 100,
      captain: "Recorded Player", transfers_made: 1, transfer_details: [{ out: "Old", in: "New" }], chips_used: [{ name: "wildcard" }],
      squad: Array.from({ length: 15 }, (_, slot) => [index, slot === 0 ? 2 : slot < 11 ? 1 : 0, slot === 0, slot === 1] as [number, number, boolean, boolean]) })) };
}

test("preserves recorded prices, bench order, captain flags, transfers and provenance", () => {
  const decoded = decodeCompactLeague(payload());
  expect(decoded.managers).toHaveLength(2);
  expect(decoded.managers[0].squad[0]).toMatchObject({ cost: 5, multiplier: 2, is_captain: true, selected_by: 10 });
  expect(decoded.managers[1].squad[0]).toMatchObject({ cost: 5.5, selected_by: undefined });
  expect(decoded.managers[0].squad[14].multiplier).toBe(0);
  expect(decoded.managers[0].squad[1].is_vice_captain).toBe(true);
  expect(decoded.managers[0].transfer_details).toEqual([{ out: "Old", in: "New" }]);
  expect(decoded.managers[0].chips_used).toEqual([{ name: "wildcard" }]);
  expect(decoded.meta?.snapshot_at).toBe("2026-10-01T08:00:00Z");
});

for (const defect of ["version", "count", "partial", "reference", "duplicate"] as const) {
  test(`rejects ${defect} instead of silently using partial research`, () => {
    const value = payload();
    if (defect === "version") value.encoding = "unknown" as CompactLeague["encoding"];
    if (defect === "count") value.declared_count = 3;
    if (defect === "partial") value.managers[0].squad.pop();
    if (defect === "reference") value.managers[0].squad[0][0] = 999;
    if (defect === "duplicate") value.managers[1].entry_id = 1;
    expect(() => decodeCompactLeague(value)).toThrow();
  });
}
