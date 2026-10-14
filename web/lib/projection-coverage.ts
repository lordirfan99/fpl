import type { LineupPlayer } from './lineup';

/** Unknown model inputs cannot become a numeric zero for the captured squad. */
export function capturedXiPoints(lineup: LineupPlayer[], bench: LineupPlayer[], captainId?: number): number | undefined {
  if (lineup.length !== 11 || bench.length !== 4 || captainId == null) return undefined;
  if (![...lineup, ...bench].every(p => typeof p.xpts === 'number' && Number.isFinite(p.xpts))) return undefined;
  const captain = lineup.find(p => p.id === captainId);
  if (!captain) return undefined;
  return lineup.reduce((sum, p) => sum + p.xpts!, 0) + captain.xpts!;
}
