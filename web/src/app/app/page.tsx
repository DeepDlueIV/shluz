import type { Metadata } from "next";
import GuestChat from "../../components/GuestChat";

export const metadata: Metadata = {
  title: "Chat — NoClip",
  robots: { index: false, follow: false },
};

export default function ChatPage() {
  return <GuestChat />;
}
