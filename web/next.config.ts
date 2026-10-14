import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Temporary redirects keep public V1 bookmarks functional while V2 is
  // validated in production. Private decision routes remain on the old path.
  async redirects() {
    return [
      { source: "/league", destination: "/v2/league", permanent: false },
      { source: "/elite", destination: "/v2/elite", permanent: false },
      { source: "/players", destination: "/v2/players", permanent: false },
      { source: "/compare", destination: "/v2/compare", permanent: false },
      { source: "/my-team", destination: "/v2/team", permanent: false },
      { source: "/journal/:season/gw/:gameweek", destination: "/v2/journal?gw=:gameweek", permanent: false },
      { source: "/journal", destination: "/v2/journal", permanent: false },
    ];
  },
  async headers() {
    return ["/planner", "/api/private/:path*", "/api/auth/:path*", "/sign-in"].map(source => ({
      source, headers: [{ key: "Cache-Control", value: "private, no-store" }, { key: "X-Robots-Tag", value: "noindex" }],
    }));
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "resources.premierleague.com" },
      { protocol: "https", hostname: "fantasy.premierleague.com" },
    ],
  },
};

export default nextConfig;
