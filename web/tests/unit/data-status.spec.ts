import { test, expect } from "@playwright/test";
import { snapshotDataStatus } from "../../lib/data-status";
const now = Date.parse("2026-10-08T12:00:00Z");
const input = { gameweek: 5, requestedGameweek: 5, asOf: "2026-10-08T11:00:00Z", isLive: true, loaded: 100, expected: 100 };
test("live data uses source age, complete squads and requested gameweek", () => {
  expect(snapshotDataStatus(input, now)).toMatchObject({ stale: false, quality: "valid", freshnessMinutes: 60 });
  expect(snapshotDataStatus({ ...input, loaded: 90 }, now).quality).toBe("partial");
  expect(snapshotDataStatus({ ...input, gameweek: 4 }, now).stale).toBe(true);
  expect(snapshotDataStatus({ ...input, asOf: undefined }, now).stale).toBe(true);
  expect(snapshotDataStatus({ ...input, asOf: "2026-10-08T12:10:00Z" }, now).stale).toBe(true);
  expect(snapshotDataStatus({ ...input, asOf: "2026-10-07T12:00:00Z" }, now).stale).toBe(true);
});
test("explicit archived results stay final; they are not current live scores", () => {
  expect(snapshotDataStatus({ ...input, isLive: false, asOf: "2026-09-01T12:00:00Z" }, now)).toMatchObject({ stale: false, isFinal: true, isLive: false });
  expect(snapshotDataStatus({ ...input, isLive: false, gameweek: 4 }, now).stale).toBe(true);
});
