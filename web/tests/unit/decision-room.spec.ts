import { test, expect } from "@playwright/test";
import { displayNumber, weeklyPoints, type DecisionPacket } from "../../lib/decision-room";

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
