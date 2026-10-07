import { test, expect } from "@playwright/test";
import { simulate } from "../../lib/transfer-simulator";
import type { DecisionPacket } from "../../lib/decision-room";
// @ts-expect-error Synthetic fixture intentionally shared with the Node server.
import { packet } from "../fixtures/decision-packet.mjs";
const fixture = (): DecisionPacket => packet();

test("complete package changes captain, legal XI and bank using actual selling price", () => {
  const p = fixture();
  p.players[15].xpts_by_gw = [10, 12, 8];
  p.players[15].cost = 57;
  p.account.picks[10].selling_price = 52;
  const result = simulate(p, [{ out: 11, in: 16 }]);
  expect(result.errors).toEqual([]);
  expect(result.bank).toBe(0.5);
  expect(result.hit).toBe(0);
  expect(result.weeks[0].draft.captain).toBe(16);
  expect(result.weeks[0].draft.starters).toHaveLength(11);
  expect(result.weeks[0].draft.bench).toHaveLength(4);
  expect(result.net).toBe(30);
});

test("hit charged only once across horizon; missing forecasts never imply zero", () => {
  const p = fixture();
  p.account.transfers.limit = 0;
  const result = simulate(p, [{ out: 11, in: 16 }]);
  expect(result.hit).toBe(4);
  expect(result.net).toBe(-4);
  p.players[15].xpts_by_gw![1] = null;
  expect(simulate(p, [{ out: 11, in: 16 }]).net).toBeNull();
  expect(simulate(p, [{ out: 11, in: 16 }]).errors[0]).toContain("projections are unavailable");
});

test("overspend, duplicates, wrong position, club limits and unknown FT block gain", () => {
  const p = fixture();
  p.players[15].cost = 61;
  expect(simulate(p, [{ out: 11, in: 16 }]).errors[0]).toContain("budget");
  p.players[15].cost = 50;
  expect(simulate(p, [{ out: 11, in: 16 }, { out: 11, in: 16 }]).errors[0]).toContain("only once");
  expect(simulate(p, [{ out: 2, in: 16 }]).errors[0]).toContain("same position");
  p.players[15].club = 2;
  expect(simulate(p, [{ out: 11, in: 16 }]).errors[0]).toContain("three players");
  p.account.transfers.made = null;
  expect(simulate(p, []).net).toBeNull();
});

test("active chips apply only in the first GW; Free Hit never projects returning squad", () => {
  const p = fixture();
  p.account.chips = [{ name: "bboost", status_for_entry: "active", played_by_entry: [3] }];
  expect(simulate(p, []).weeks.map(w => w.draft.points)).toEqual([80, 72, 48]);
  p.account.chips[0].name = "3xc";
  expect(simulate(p, []).weeks.map(w => w.draft.points)).toEqual([65, 72, 48]);
  p.account.chips[0].name = "freehit";
  p.chip = "freehit";
  p.account.transfers.unlimited = null;
  p.account.transfers.status = "unlimited";
  p.account.transfers.limit = null;
  expect(simulate(p, [{ out: 11, in: 16 }]).weeks).toHaveLength(1);
  expect(simulate(p, [{ out: 11, in: 16 }]).hit).toBe(0);
});

test("multi-transfer package budgets jointly and uses captured catalog", () => {
  const p = fixture();
  p.simulation_players = [...p.players, { ...p.players[1], id: 17, name: "New defender", cost: 60, xpts_by_gw: [9, 9, 9] }];
  p.simulation_players[15] = { ...p.players[15], cost: 40, xpts_by_gw: [10, 10, 10] };
  // Explicit existing player evidence is canonical; new pool entries supply new players.
  p.players[15].cost = 40;
  const result = simulate(p, [{ out: 11, in: 16 }, { out: 2, in: 17 }]);
  expect(result.errors).toEqual([]);
  expect(result.bank).toBe(1);
  expect(result.hit).toBe(0);
  expect(result.weeks[0].draft.starters).toContain(17);
});
