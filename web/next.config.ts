import type { NextConfig } from "next";

const gateway = process.env.SHLUZ_GATEWAY_ORIGIN?.replace(/\/$/, "");
const nextConfig: NextConfig = {
  poweredByHeader: false,
  ...(process.env.NOCLIP_STATIC_EXPORT === "1" ? { output: "export", trailingSlash: true } : {}),
  images: { unoptimized: true },
  ...(gateway && process.env.NOCLIP_STATIC_EXPORT !== "1" ? {
    async rewrites() { return [{ source: "/v1/web/:path*", destination: `${gateway}/v1/web/:path*` }]; },
  } : {}),
};
export default nextConfig;
