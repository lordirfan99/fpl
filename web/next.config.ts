import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return ["/this-week", "/api/private/:path*", "/api/auth/:path*", "/sign-in"].map(source => ({
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
