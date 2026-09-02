import "server-only";
import type { BootstrapEvent } from "./types";

// Single source of truth for "where are we in the season". Every page derives
// its target gameweek and deadline from this instead of re-scanning events its
// own way, so Assistant, My Team, Planner and Players can never disagree.
export interface SeasonContext {
  /** Latest finalized/collected league snapshot the dashboard is showing. */
  finalizedGw: number;
  /** Gameweek currently being played, per official FPL. */
  liveGw: number;
  /** The gameweek whose deadline is the next one to act on. */
  nextDeadlineGw: number;
  nextDeadline: string | null;
  /** Hours until nextDeadline, clamped at 0; null when unknown. */
  hoursToDeadline: number | null;
  deadlinePassed: boolean;
}

export function deriveSeasonContext(
  events: BootstrapEvent[],
  opts: { finalizedGw: number; liveGameweek?: number },
): SeasonContext {
  const byId = (id: number) => events.find((event) => event.id === id);
  const current = events.find((event) => event.is_current);
  const liveGw = current?.id ?? opts.liveGameweek ?? opts.finalizedGw;

  // Prefer FPL's own `is_next` flag; fall back to the first event that is
  // neither finished nor in progress, then to liveGw + 1.
  const nextEvent =
    events.find((event) => event.is_next) ??
    events.find((event) => !event.finished && !event.is_current) ??
    byId(liveGw + 1) ??
    null;

  const nextDeadlineGw = nextEvent?.id ?? liveGw;
  const nextDeadline = nextEvent?.deadline_time ?? null;
  const rawHours = nextDeadline ? (new Date(nextDeadline).getTime() - Date.now()) / 3_600_000 : null;

  return {
    finalizedGw: opts.finalizedGw,
    liveGw,
    nextDeadlineGw,
    nextDeadline,
    hoursToDeadline: rawHours == null ? null : Math.max(0, rawHours),
    deadlinePassed: rawHours != null && rawHours <= 0,
  };
}
