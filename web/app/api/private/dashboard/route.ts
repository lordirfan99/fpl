import { isOwner } from "@/auth";
import { getPrivateDashboard } from "@/lib/private-dashboard";

export const dynamic = "force-dynamic";
export async function GET() {
  const headers = { "Cache-Control": "private, no-store", Vary: "Cookie" };
  if (!await isOwner()) return Response.json({ error: "Unauthorized" }, { status: 401, headers });
  // Stay under Netlify's 10s synchronous-function kill limit so a cold
  // upstream degrades to a clean 503 response, never a platform 502.
  return Response.json(await getPrivateDashboard(9000), { headers });
}
