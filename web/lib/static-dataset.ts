import { decodeCompactLeague, type CompactLeague } from "./compact-league";

export interface StaticManifest {
  schema_version: 1; release: string; revision: string; generated_at: string;
  files: Record<string, string>; gameweeks: Record<string, number[]>; writes_enabled: false;
}
export class StaticDataError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function staticFreshness(captured: unknown, now = Date.now()) {
  const age = typeof captured === "string" ? (now - Date.parse(captured)) / 3_600_000 : NaN;
  return { freshness_hours: Number.isFinite(age) ? Math.round(age * 100) / 100 : null,
    stale: !Number.isFinite(age) || age < -5 / 60 || age > 12, max_age_hours: 12 };
}
const publicFile = /^(?:(?:catalog|fixtures|projections)\.json|leagues\/(?:58005|131997)\/(?:(?:live|decision|team|research)\.json|gw\/(?:[1-9]|[12]\d|3[0-8])\.json)|journal\/2026-27\/(?:index\.json|gameweeks\.csv|gw\/(?:[1-9]|[12]\d|3[0-8])\.json))$/;
type RecordData = Record<string, unknown>;
export class StaticDataset {
  private cached?: { expires: number; value: StaticManifest };
  private pending?: Promise<StaticManifest>;
  private bodies = new Map<string, Promise<string>>();
  constructor(private base: string, private fetcher: (url: string) => Promise<Response>) {
    const url = new URL(base);
    if (url.username || url.password || url.search || url.hash) throw new Error("Invalid public data URL");
    this.base = base.replace(/\/$/, "");
  }
  async manifest(): Promise<StaticManifest> {
    if (this.cached && this.cached.expires > Date.now()) return this.cached.value;
    if (this.pending) return this.pending;
    this.pending = (async () => {
      const r = await this.fetcher(`${this.base}/manifest.json`);
      if (!r.ok) throw new StaticDataError(503, "Scheduled dataset is unavailable");
      const m = await r.json() as StaticManifest;
      if (m.schema_version !== 1 || !/^[a-f0-9]{64}$/.test(m.release) || m.writes_enabled !== false
          || !m.files || Object.keys(m.files).length > 200 || !m.gameweeks
          || Object.entries(m.files).some(([p, h]) => !publicFile.test(p) || !/^[a-f0-9]{64}$/.test(h))) {
        throw new StaticDataError(503, "Invalid scheduled dataset manifest");
      }
      this.cached = { expires: Date.now() + 30_000, value: m };
      return m;
    })();
    try { return await this.pending; } finally { this.pending = undefined; }
  }
  private async text(m: StaticManifest, path: string): Promise<string> {
    if (!publicFile.test(path) || !m.files[path]) throw new StaticDataError(404, "No recorded artifact");
    const key = `${m.release}:${path}`;
    const pending = this.bodies.get(key);
    if (pending) return pending;
    const load = (async () => {
      const r = await this.fetcher(`${this.base}/releases/${m.release}/${path}`);
      if (!r.ok) throw new StaticDataError(503, "Recorded artifact could not load");
      const bytes = await r.arrayBuffer();
      if (bytes.byteLength > 20 * 1024 * 1024) throw new StaticDataError(503, "Recorded artifact exceeds size limit");
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const hex = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, "0")).join("");
      if (hex !== m.files[path]) throw new StaticDataError(503, "Recorded artifact integrity check failed");
      return new TextDecoder().decode(bytes);
    })();
    this.bodies.set(key, load);
    while (this.bodies.size > 16) this.bodies.delete(this.bodies.keys().next().value!);
    try { return await load; } catch (error) { this.bodies.delete(key); throw error; }
  }
  private async json<T>(m: StaticManifest, path: string): Promise<T> { return JSON.parse(await this.text(m, path)) as T; }
  private async league(m: StaticManifest, id: string, gw?: number, latest = false): Promise<CompactLeague> {
    const weeks = m.gameweeks[id];
    if (!Array.isArray(weeks) || weeks.some(w => !Number.isInteger(w) || w < 1 || w > 38)) throw new StaticDataError(503, "Invalid archive index");
    const chosen = latest ? Math.max(0, ...weeks.filter(w => gw === undefined || w <= gw)) : gw;
    const file = chosen ? `leagues/${id}/gw/${chosen}.json` : `leagues/${id}/live.json`;
    if (!chosen && (!latest || gw !== undefined)) throw new StaticDataError(404, "No finalized archive for this gameweek");
    const data = await this.json<CompactLeague & { league_id: number }>(m, file);
    if (data.league_id !== Number(id) || (chosen && (data.gameweek !== chosen || data.provisional))) throw new StaticDataError(503, "Archive identity mismatch");
    if (!chosen && staticFreshness(data.meta?.snapshot_at).stale) throw new StaticDataError(503, "Captured standings are stale");
    decodeCompactLeague(data);
    return data;
  }
  async request(url: URL): Promise<Response> {
    try { return Response.json(await this.resolve(url)); }
    catch (error) { return Response.json({ detail: error instanceof Error ? error.message : "Scheduled data unavailable" },
      { status: error instanceof StaticDataError ? error.status : 503 }); }
  }
  private async resolve(url: URL): Promise<unknown> {
    const prefix = url.pathname.indexOf("/v1/");
    const path = prefix < 0 ? url.pathname : url.pathname.slice(prefix), q = url.searchParams;
    // Private and unsupported routes are never fetched from the public host.
    const leagueMatch = path.match(/^\/v1\/leagues\/(58005|131997)\/(compact|summary|directory|managers\/\d+|live\/(?:compact|status)|decision-context)$/);
    const journalMatch = path.match(/^\/v1\/journal\/2026-27\/gw\/(?:[1-9]|[12]\d|3[0-8])$/);
    if (!leagueMatch && !journalMatch && !["/v1/catalog/compact", "/v1/fixtures", "/v1/projections/current", "/v1/journal", "/v1/live/team", "/v1/decision/current"].includes(path)) {
      throw new StaticDataError(404, "Feature unavailable in scheduled public data");
    }
    const m = await this.manifest();
    if (path === "/v1/catalog/compact" || path === "/v1/projections/current") {
      const data = await this.json<RecordData>(m, path.includes("catalog") ? "catalog.json" : "projections.json");
      const meta = data.meta as RecordData, freshness = staticFreshness(meta.snapshot_at);
      if (path.includes("projections") && freshness.stale) throw new StaticDataError(503, "Model inputs are stale");
      return { ...data, meta: { ...meta, ...freshness } };
    }
    if (path === "/v1/fixtures") {
      const from = Number(q.get("from_gw")), to = Number(q.get("to_gw"));
      if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to > 38 || to < from || to - from > 9) throw new StaticDataError(400, "Invalid fixture horizon");
      const data = await this.json<{ gameweeks: Record<string, unknown> }>(m, "fixtures.json");
      return { from_gameweek: from, to_gameweek: to, gameweeks: Object.fromEntries(Array.from({ length: to - from + 1 }, (_, i) => [String(from + i), data.gameweeks[String(from + i)]])) };
    }
    if (path === "/v1/journal") {
      if (q.has("season") && q.get("season") !== "2026-27") throw new StaticDataError(404, "Season unavailable");
      return this.json(m, "journal/2026-27/index.json");
    }
    if (journalMatch) return this.json(m, `journal/2026-27/gw/${path.split("/").at(-1)}.json`);
    if (path === "/v1/decision/current") {
      const id = q.get("league_id") ?? "58005";
      const data = await this.json<RecordData>(m, `leagues/${id}/research.json`);
      if (q.has("gw") && Number(q.get("gw")) !== data.gameweek) throw new StaticDataError(404, "Research capture is for another gameweek");
      const fresh = data.freshness as RecordData;
      const freshness = staticFreshness(fresh.snapshot_at);
      return { ...data, transfers: [], captains: [], plan: null, executable: false, writes_enabled: false,
        freshness: { ...fresh, ...freshness, status: freshness.stale ? "needs_refresh" : "provisional",
          data_age_hours: freshness.freshness_hours, account_state_verified: false },
        packet_status: freshness.stale ? "needs_refresh" : "advisory" };
    }
    if (path === "/v1/live/team") {
      const id = q.get("league_id") ?? "58005";
      const data = await this.json<RecordData>(m, `leagues/${id}/team.json`);
      if (q.has("gw") && Number(q.get("gw")) !== data.gameweek) throw new StaticDataError(404, "Capture is for another gameweek");
      if (staticFreshness(data.fetched_at).stale) throw new StaticDataError(503, "Public team capture is stale");
      return data;
    }
    const [, id, kind] = leagueMatch!;
    if (kind === "decision-context") {
      const data = await this.json<RecordData>(m, `leagues/${id}/decision.json`);
      const freshness = staticFreshness(data.snapshot_at);
      return { ...data, freshness, status: freshness.stale ? "historical" : data.status };
    }
    if (kind.startsWith("live/")) {
      const data = await this.json<CompactLeague>(m, `leagues/${id}/live.json`);
      decodeCompactLeague(data);
      const freshness = staticFreshness(data.meta?.snapshot_at);
      return kind === "live/status" ? { ready: !freshness.stale, league_id: Number(id), gameweek: data.gameweek,
        captured_at: data.meta?.snapshot_at, manager_count: data.count, ...freshness } : { ...data, meta: { ...data.meta, ...freshness } };
    }
    const gw = q.has("gw") ? Number(q.get("gw")) : undefined;
    if (gw !== undefined && (!Number.isInteger(gw) || gw < 1 || gw > 38)) throw new StaticDataError(400, "Invalid gameweek");
    // Selected manager may come from the current captured provisional directory.
    const isManager = kind.startsWith("managers/");
    const data = isManager && gw !== undefined && !m.gameweeks[id].includes(gw)
      ? await this.json<CompactLeague>(m, `leagues/${id}/live.json`)
      : await this.league(m, id, gw, kind !== "compact" && !isManager);
    if (isManager && data.gameweek !== gw) throw new StaticDataError(404, "Manager capture is for another gameweek");
    if (kind === "compact") return data;
    const all = decodeCompactLeague(data).managers.sort((a, b) => a.league_rank - b.league_rank);
    const managers = all.map(({ squad: _squad, ...row }) => row);
    if (kind === "directory") return { gameweek: data.gameweek, managers, provisional: data.provisional, meta: data.meta };
    if (isManager) {
      const manager = all.find(row => row.entry_id === Number(kind.split("/")[1]));
      if (!manager) throw new StaticDataError(404, "Manager not in recorded league");
      return manager;
    }
    const term = (q.get("q") ?? "").trim().toLowerCase();
    const filtered = managers.filter(row => !term || `${row.entry_name} ${row.player_name} ${row.entry_id} ${row.league_rank}`.toLowerCase().includes(term));
    const size = Math.max(10, Math.min(100, Math.floor(Number(q.get("page_size")) || 50)));
    const pages = Math.max(1, Math.ceil(filtered.length / size));
    const page = Math.max(1, Math.min(pages, Math.floor(Number(q.get("page")) || 1)));
    return { meta: data.meta, league_id: Number(id), gameweek: data.gameweek, provisional: data.provisional,
      total: managers.length, filtered_total: filtered.length, page, page_size: size, pages, query: term,
      average_gameweek_points: managers.reduce((sum, row) => sum + row.gw_points, 0) / managers.length,
      leader: managers[0], manager: managers.find(row => row.entry_id === 2797967), managers: filtered.slice((page - 1) * size, page * size) };
  }
  async exportUrl(): Promise<string> {
    const m = await this.manifest(), file = "journal/2026-27/gameweeks.csv";
    if (!m.files[file]) throw new StaticDataError(404, "Journal CSV unavailable");
    return `${this.base}/releases/${m.release}/${file}`;
  }
}
