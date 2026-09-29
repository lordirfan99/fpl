// Keeps the public league-decision endpoints warm. A cold upstream API build
// can take longer than the route timeout, which would surface as "evidence
// unavailable" for the first visitor after each data-cache expiry. This runs
// every 4 minutes via the Netlify scheduler so real visitors always hit a
// warm cache.
const FALLBACK_SITE = "https://fpl-scout-intelligence.netlify.app";

export default async () => {
  const base = process.env.DEPLOY_PRIME_URL || FALLBACK_SITE;
  const targets = [
    `${base}/api/public/league-decision/58005`,
    `${base}/api/public/league-decision/131997`,
  ];
  const results = await Promise.allSettled(targets.map(async (url) => {
    // Scheduled functions run with generous limits; out-wait a cold upstream
    // build so the cache actually fills instead of being killed at 10s.
    const response = await fetch(url, { signal: AbortSignal.timeout(120000) });
    return `${response.status} ${new URL(url).pathname}`;
  }));
  const summary = results.map((result) => (result.status === "fulfilled" ? result.value : "error"));
  console.log(`keep-warm: ${summary.join(", ")}`);
  return new Response(null, { status: 204 });
};

export const config = { schedule: "*/4 * * * *" };
