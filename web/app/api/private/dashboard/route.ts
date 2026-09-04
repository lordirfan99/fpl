import { isOwner } from "@/auth";
import { getPrivateDashboard } from "@/lib/private-dashboard";

export const dynamic = "force-dynamic";
export async function GET() {
  const headers = { "Cache-Control": "private, no-store", Vary: "Cookie" };
  if (!await isOwner()) return Response.json({ error: "Unauthorized" }, { status: 401, headers });
  return Response.json(await getPrivateDashboard(), { headers });
}
