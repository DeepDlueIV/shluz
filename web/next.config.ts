import type { NextConfig } from "next";

const gateway = process.env.SHLUZ_GATEWAY_ORIGIN?.replace(/\/$/, "");
const protectedBridge = Boolean(process.env.SHLUZ_BRIDGE_ORIGIN);
const nextConfig: NextConfig = {
  poweredByHeader: false,
  ...(process.env.NOCLIP_STATIC_EXPORT === "1" ? { output: "export", trailingSlash: true } : {}),
  images: { unoptimized: true },
  ...(gateway && !protectedBridge && process.env.NOCLIP_STATIC_EXPORT !== "1" ? {
    async rewrites() { return [{ source: "/v1/web/:path*", destination: gateway + "/v1/web/:path*" }]; },
  } : {}),
};
export default nextConfig;
