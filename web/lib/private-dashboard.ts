import "server-only";
import { isOwner } from "@/auth";

export async function getPrivateDashboard() {
  if (!await isOwner()) return { status: "signed_out", packet: null };
  const token = process.env.FPL_DASHBOARD_READ_TOKEN;
  if (!token || token.length < 32) return { status: "unavailable", packet: null };
  try {
    const base = process.env.FPL_API_BASE_URL ?? "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app";
    const response = await fetch(`${base}/v1/private/dashboard/current`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) return { status: "unavailable", packet: null };
    return await response.json();
  } catch { return { status: "unavailable", packet: null }; }
}
