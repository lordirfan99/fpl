import { getLeagueDecision } from "@/lib/league-decision";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const value = Number(new URL(request.url).searchParams.get("league"));
  if (![58005, 131997].includes(value)) return Response.json({ error: "Unsupported league" }, { status: 400 });
  const context = await getLeagueDecision(value);
  if (!context) return Response.json({ error: "League evidence unavailable" }, { status: 503 });
  return Response.json(context, { headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" } });
}
