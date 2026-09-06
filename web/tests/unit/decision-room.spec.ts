import { test, expect } from "@playwright/test";
import { captainVsField, displayNumber, weeklyPoints, type DecisionPacket } from "../../lib/decision-room";

test("missing values never become zero or a confidence claim", () => {
  expect(displayNumber(null)).toBe("Unavailable");
  expect(displayNumber(undefined)).toBe("Unavailable");
  expect(displayNumber(NaN)).toBe("Unavailable");
  expect(displayNumber(0)).toBe("0.0");
});
test("weekly bars count XI and captain, not full squad, and reject missing data", () => {
  const packet = { account: { chips: [] }, players: Array.from({ length: 15 }, (_, i) => ({ id: i + 1, xpts_by_gw: [5, null, 3] })) } as unknown as DecisionPacket;
  const xi = Array.from({ length: 11 }, (_, i) => i + 1);
  expect(weeklyPoints(packet, xi, 1, 0)).toBe(60);
  expect(weeklyPoints(packet, xi, 1, 1)).toBeNull();
  expect(weeklyPoints(packet, xi, undefined, 0)).toBeNull();
  expect(weeklyPoints(packet, xi.slice(1), 1, 0)).toBeNull();
  packet.account.chips = [{ name: "3xc", status_for_entry: "active", played_by_entry: [3] }];
  expect(weeklyPoints(packet, xi, 1, 0)).toBe(65);
  expect(weeklyPoints(packet, xi, 1, 2)).toBe(36);
});

const captainPacket = (captain: number, opts: { ownFieldCaptain?: boolean } = {}): DecisionPacket => ({
  captain,
  captains: [{ id: 10, name: "Haaland", xpts: 6.4 }, { id: 20, name: "Salah", xpts: 5.1 }],
  players: [{ id: 10, name: "Haaland", xpts: 6.4 }, { id: 20, name: "Salah", xpts: 5.1 }, { id: 30, name: "Palmer", xpts: 5.9 }],
  account: { picks: (opts.ownFieldCaptain === false ? [20, 30] : [10, 20, 30]).map((element) => ({ element })) },
} as unknown as DecisionPacket);

test("captainVsField: no field captain -> null", () => {
  expect(captainVsField(captainPacket(10), undefined)).toBeNull();
});

test("captainVsField: your captain is the field captain -> aligned, no risk", () => {
  const vs = captainVsField(captainPacket(10), { element: 10, name: "Haaland", pct: 61 })!;
  expect(vs.aligned).toBe(true);
  expect(vs.deltaXpts).toBe(0);
  expect(vs.verdict).toContain("also the target group's top pick");
});

test("captainVsField: differ on an owned player reports the projected-points delta", () => {
  const vs = captainVsField(captainPacket(30), { element: 10, name: "Haaland", pct: 55 })!;
  expect(vs.aligned).toBe(false);
  expect(vs.field.owned).toBe(true);
  expect(vs.deltaXpts).toBe(-0.5); // Palmer 5.9 - Haaland 6.4
  expect(vs.verdict).toContain("0.5 pts below Haaland");
});

test("captainVsField: differ on a player you do not own flags the EO exposure", () => {
  const vs = captainVsField(captainPacket(30, { ownFieldCaptain: false }), { element: 10, name: "Haaland", pct: 55 })!;
  expect(vs.field.owned).toBe(false);
  expect(vs.verdict).toContain("do not own Haaland");
});
