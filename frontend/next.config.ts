import type { NextConfig } from "next";

// The local FastAPI backend (bound to 127.0.0.1). The browser only ever talks to Next.js,
// which proxies /api and /reports to it — no CORS, nothing exposed beyond this machine.
const BACKEND = process.env.NEXTRADE_BACKEND ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/reports/:path*", destination: `${BACKEND}/reports/:path*` },
    ];
  },
};

export default nextConfig;
