import type { DataStatus } from "./types";

export function snapshotDataStatus(input: {
  gameweek: number; requestedGameweek: number; asOf?: string; isLive: boolean;
  loaded: number; expected: number;
}, now = Date.now()): DataStatus {
  const captured = input.asOf ? Date.parse(input.asOf) : NaN;
  const age = (now - captured) / 60_000;
  const known = Number.isFinite(age) && age >= -5;
  const olderGameweek = input.gameweek !== input.requestedGameweek;
  return {
    source: input.isLive ? "official-fpl-live" : "finalized-snapshot",
    gameweek: input.gameweek, asOf: input.asOf, isLive: input.isLive, isFinal: !input.isLive,
    stale: olderGameweek || !known || (input.isLive && age > 12 * 60),
    freshnessMinutes: known ? Math.max(0, age) : undefined,
    quality: input.expected <= 0 ? "unavailable" : input.loaded < input.expected ? "partial" : "valid",
    fallbackReason: olderGameweek ? "Showing an older recorded gameweek." : !known ? "Capture time could not be verified." : undefined,
    hydration: { loaded: input.loaded, expected: input.expected, percent: Math.round(input.loaded / Math.max(1, input.expected) * 100) },
  };
}
