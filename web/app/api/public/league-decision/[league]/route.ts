import { getLeagueDecision } from "@/lib/league-decision";

export const dynamic = "force-dynamic";

const SUPPORTED_LEAGUES = [58005, 131997];

export async function GET(_request: Request, { params }: { params: Promise<{ league: string }> }) {
  const value = Number((await params).league);
  if (!SUPPORTED_LEAGUES.includes(value)) return Response.json({ error: "Unsupported league" }, { status: 400 });
  const context = await getLeagueDecision(value);
  if (!context) return Response.json({ error: "League evidence unavailable" }, { status: 503 });
  return Response.json(context, { headers: { "Cache-Control": "public, max-age=240, stale-while-revalidate=600" } });
}
