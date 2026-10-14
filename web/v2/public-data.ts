import "server-only";
import { createHash } from "node:crypto";
import { cache } from "react";
import { decodeCompactLeague, type CompactLeague } from "@/lib/compact-league";
import type { Manager } from "@/lib/types";

// V2 never falls back from public Pages reads to the unstable private VM.
// Return an explicit status rather than throwing from a Server Component.
const BASE = (process.env.FPL_PUBLIC_DATA_URL || "https://lordirfan99.github.io/fpl").replace(/\/$/, "");
const LEAGUES: Record<number, string> = { 58005: "KK Old Boys", 131997: "Overall IFE" };

export type DataResult<T> =
  | { ok: true; data: T; capturedAt: string | null; stale: boolean; release: string; availableWeeks: number[] }
  | { ok: false; code: "missing" | "network" | "invalid" | "integrity"; message: string; availableWeeks: number[] };

export type Catalog = {
  meta?: { snapshot_at?: string; quality_status?: string };
  players: Array<{ id: number; web_name: string; team: number; element_type: number; now_cost: number;
    ep_next?: string | null; form?: string | null; selected_by_percent?: string | null; status?: string; news?: string }>;
  teams: Array<{ id: number; short_name: string; name: string }>;
  events: Array<{ id: number; is_current?: boolean; is_next?: boolean; finished?: boolean; data_checked?: boolean; deadline_time?: string }>;
};
export type LeagueData = { id: number; name: string; gameweek: number; provisional: boolean; managers: Manager[] };
type Manifest = { schema_version: 1; release: string; files: Record<string, string>; gameweeks: Record<string, number[]>; writes_enabled: false };

class GatewayError extends Error {
  constructor(public code: "missing" | "network" | "invalid" | "integrity", message: string) { super(message); }
}
function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new GatewayError("invalid", message);
}
async function requestBytes(url: string): Promise<Buffer> {
  let response: Response;
  try {
    response = await fetch(url, { next: { revalidate: 45 }, signal: AbortSignal.timeout(8000) });
  } catch {
    throw new GatewayError("network", "Public data service is temporarily unreachable.");
  }
  if (response.status === 404) throw new GatewayError("missing", "This gameweek has not been published.");
  if (!response.ok) throw new GatewayError("network", "Public data service is temporarily unavailable.");
  const buffer = Buffer.from(await response.arrayBuffer());
  assert(buffer.byteLength <= 20 * 1024 * 1024, "Public snapshot exceeds the verified size limit.");
  return buffer;
}
const loadManifest = cache(async (): Promise<Manifest> => {
  const payload: unknown = JSON.parse((await requestBytes(BASE + "/manifest.json")).toString("utf8"));
  assert(!!payload && typeof payload === "object", "Invalid manifest response.");
  const m = payload as Partial<Manifest>;
  assert(m.schema_version === 1 && m.writes_enabled === false &&
    typeof m.release === "string" && /^[0-9a-f]{64}$/.test(m.release) &&
    !!m.files && typeof m.files === "object" && !!m.gameweeks && typeof m.gameweeks === "object",
    "Manifest integrity validation failed.");
  return m as Manifest;
});
async function loadFile<T>(manifest: Manifest, name: string): Promise<T> {
  const digest = manifest.files[name];
  if (!digest) throw new GatewayError("missing", "Requested snapshot is not in the published dataset.");
  assert(/^[0-9a-f]{64}$/.test(digest), "Invalid artifact digest.");
  const bytes = await requestBytes(BASE + "/releases/" + manifest.release + "/" + name);
  const hash = createHash("sha256").update(bytes).digest("hex");
  if (hash !== digest) throw new GatewayError("integrity", "Snapshot hash does not match published evidence.");
  try { return JSON.parse(bytes.toString("utf8")) as T; }
  catch { throw new GatewayError("invalid", "Published snapshot is not valid JSON."); }
}
function ageOf(value: string | undefined | null) {
  const parsed = value ? Date.parse(value) : NaN;
  const hours = (Date.now() - parsed) / 3_600_000;
  return !Number.isFinite(hours) || hours < -5 / 60 || hours > 12;
}
function failure(error: unknown, availableWeeks: number[] = []): DataResult<never> {
  if (error instanceof GatewayError) return { ok: false, code: error.code, message: error.message, availableWeeks };
  return { ok: false, code: "invalid", message: "Published data did not pass validation.", availableWeeks };
}
export const getCatalogV2 = cache(async (): Promise<DataResult<Catalog>> => {
  try {
    const manifest = await loadManifest();
    const data = await loadFile<Catalog>(manifest, "catalog.json");
    assert(Array.isArray(data.players) && data.players.length >= 15 &&
      Array.isArray(data.teams) && Array.isArray(data.events), "Incomplete official player catalog.");
    return { ok: true, data, capturedAt: data.meta?.snapshot_at || null,
      stale: ageOf(data.meta?.snapshot_at), release: manifest.release, availableWeeks: [] };
  } catch (error) { return failure(error); }
});

export function leagueId(value: unknown) { const id = Number(value); return id === 131997 ? 131997 : 58005; }
export function leagueName(id: number) { return LEAGUES[id] || "Unknown league"; }
export function gwParam(value: unknown): number | null | "invalid" {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 38 ? n : "invalid";
}
export const getLeagueV2 = cache(async (id: number, requestedGw: number | null | "invalid" = null): Promise<DataResult<LeagueData>> => {
  try {
    assert(id in LEAGUES, "Unsupported tracked league.");
    const manifest = await loadManifest();
    const availableWeeks = manifest.gameweeks[String(id)] || [];
    assert(Array.isArray(availableWeeks) && availableWeeks.every(w => Number.isInteger(w) && w >= 1 && w <= 38),
      "Invalid published gameweek index.");
    if (requestedGw === "invalid") return { ok: false, code: "invalid", message: "Select a valid gameweek (1–38).", availableWeeks };
    if (requestedGw !== null && !availableWeeks.includes(requestedGw)) {
      return { ok: false, code: "missing", message: `GW${requestedGw} is not archived for ${leagueName(id)}. Select a verified week below.`, availableWeeks };
    }
    const name = requestedGw === null ? `leagues/${id}/live.json` : `leagues/${id}/gw/${requestedGw}.json`;
    const raw = await loadFile<CompactLeague & { league_id: number }>(manifest, name);
    assert(raw.league_id === id && Number.isInteger(raw.gameweek) &&
      (requestedGw === null || (raw.gameweek === requestedGw && raw.provisional !== true)),
      "League snapshot identity or finalization mismatch.");
    const decoded = decodeCompactLeague(raw);
    assert(decoded.managers.length > 0 && decoded.managers.every(m => m.squad?.length === 15),
      "Complete squads were not published.");
    return { ok: true,
      data: { id, name: leagueName(id), gameweek: raw.gameweek,
        provisional: requestedGw === null || raw.provisional === true,
        managers: decoded.managers },
      capturedAt: raw.meta?.snapshot_at ?? raw.meta?.generated_at ?? null,
      stale: ageOf(raw.meta?.snapshot_at || raw.meta?.generated_at), release: manifest.release, availableWeeks };
  } catch (error) { return failure(error); }
});
export const MY_TEAM = 2797967;
