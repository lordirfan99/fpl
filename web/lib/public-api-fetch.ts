import "server-only";
import { fetchApi } from "./api-fetch";
import { StaticDataset } from "./static-dataset";

// Public recorded research only. Account/session/decision requests must keep
// using fetchApi with no-store. Preserve each payload's original capture time.
export const PUBLIC_API_TIMEOUT_MS = 8000;
export const PUBLIC_REVALIDATE_SECONDS = 60;

const staticBase = process.env.FPL_PUBLIC_DATA_URL?.replace(/\/$/, "");
export const staticDataset = staticBase ? new StaticDataset(staticBase, url => fetchApi(url, {
  cache: "force-cache", next: { revalidate: PUBLIC_REVALIDATE_SECONDS },
}, PUBLIC_API_TIMEOUT_MS)) : null;

export function fetchPublicApi(url: string) {
  if (staticDataset) return staticDataset.request(new URL(url));
  return fetchApi(url, {
    cache: "force-cache",
    next: { revalidate: PUBLIC_REVALIDATE_SECONDS },
  }, PUBLIC_API_TIMEOUT_MS);
}
