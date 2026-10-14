import "server-only";
import { fetchApi } from "./api-fetch";

// Public recorded research only. Account/session/decision requests must keep
// using fetchApi with no-store. Preserve each payload's original capture time.
export const PUBLIC_API_TIMEOUT_MS = 8000;
export const PUBLIC_REVALIDATE_SECONDS = 60;

export function fetchPublicApi(url: string) {
  return fetchApi(url, {
    cache: "force-cache",
    next: { revalidate: PUBLIC_REVALIDATE_SECONDS },
  }, PUBLIC_API_TIMEOUT_MS);
}
