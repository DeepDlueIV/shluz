import type { Metadata, Viewport } from "next";
import "@fontsource-variable/manrope";
import "@fontsource-variable/cormorant-garamond";
import "./globals.css";
import { GuestJourneyProvider } from "@/components/GuestJourney";
import { SitePreferences } from "@/components/SitePreferences";

export const metadata: Metadata = {
  title: "NoClip — AI chat and coding agents",
  description: "AI models for chat, writing and code. Preview the NoClip interface and guest chat.",
  icons: { icon: "/media/favicon.ico", apple: "/media/noclip-icon.png" },
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0c0d0f" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><SitePreferences><GuestJourneyProvider>{children}</GuestJourneyProvider></SitePreferences></body></html>;
}
