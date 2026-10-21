import type { Metadata, Viewport } from "next";
import "@fontsource-variable/manrope";
import "@fontsource-variable/cormorant-garamond";
import "@fontsource-variable/cormorant-garamond/wght-italic.css";
import "./globals.css";
import { GuestJourneyProvider } from "@/components/GuestJourney";

export const metadata: Metadata = {
  title: "NoClip — пространство без лишних границ",
  description: "Задавайте вопросы, исследуйте идеи и создавайте с AI. NoClip объединяет модели и инструменты в одном пространстве.",
  icons: { icon: "/media/favicon.ico", apple: "/media/noclip-icon.png" },
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0c0d0f" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body><GuestJourneyProvider>{children}</GuestJourneyProvider></body></html>;
}
