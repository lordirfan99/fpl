import { isOwner } from "@/auth";
import { getPrivateDashboard } from "@/lib/private-dashboard";

export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const headers = { "Cache-Control": "private, no-store", Vary: "Cookie" };
  if (!await isOwner()) return Response.json({ error: "Unauthorized" }, { status: 401, headers });
  const result = await getPrivateDashboard();
  if (new URL(request.url).searchParams.get("view") === "check") {
    return Response.json({ status: result.status, account_checked_at: result.account_checked_at,
      packet: result.packet ? { plan_id: result.packet.plan_id, account_fingerprint: result.packet.account_fingerprint } : null }, { headers });
  }
  return Response.json(result, { headers });
}
