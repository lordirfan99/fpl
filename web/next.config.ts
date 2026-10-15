import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin only this public URL into tagged builds so CLI deployments do not
  // depend on Functions inheriting a separate Netlify environment setting.
  // Builds without it retain runtime selection for local/API-mode tests.
  ...(process.env.FPL_PUBLIC_DATA_URL ? {
    env: { FPL_PUBLIC_DATA_URL: process.env.FPL_PUBLIC_DATA_URL },
  } : {}),
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
