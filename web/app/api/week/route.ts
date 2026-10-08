import { getLeagueDecision } from "@/lib/league-decision";
import { getPrivateDashboard } from "@/lib/private-dashboard";

export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const headers = { "Cache-Control": "private, no-store", Vary: "Cookie", "Content-Type": "application/x-ndjson; charset=utf-8" };
  const league = Number(new URL(request.url).searchParams.get("league") ?? 58005);
  if (![58005, 131997].includes(league)) return Response.json({ error: "Unsupported league" }, { status: 400, headers });
  let cancelled = false;
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (section: string, data: unknown) => { if (!cancelled) controller.enqueue(encoder.encode(JSON.stringify({ section, data }) + "\n")); };
      await Promise.allSettled([
        getLeagueDecision(league, false).then(context => send("league", context ? {
          league_id: context.league_id, gameweek: context.gameweek, status: context.status,
          snapshot_at: context.snapshot_at, freshness: context.freshness, goal: context.goal,
          ownership: { ...context.ownership, rows: context.ownership.rows.map(p => ({ element: p.element, name: p.name, target_pct: p.target_pct, target_captain_pct: p.target_captain_pct })) },
        } : null)).catch(() => send("league", null)),
        getPrivateDashboard().then(plan => send("private", plan)).catch(() => send("private", { status: "unavailable", packet: null })),
      ]);
      if (!cancelled) controller.close();
    },
    cancel() { cancelled = true; },
  });
  return new Response(stream, { headers });
}
