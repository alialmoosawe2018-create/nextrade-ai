import type { Metadata } from "next";
import { IBM_Plex_Sans_Arabic } from "next/font/google";
import "./globals.css";

import { Shell } from "@/components/Shell";
import { I18nProvider } from "@/lib/i18n";

const plex = IBM_Plex_Sans_Arabic({
  variable: "--font-plex-arabic",
  subsets: ["arabic", "latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "NexTrade AI",
  description: "Private Nasdaq research and paper-trading platform (simulation only)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ar" dir="rtl" data-theme="dark" className={`${plex.variable} h-full antialiased`}>
      <body className="min-h-full">
        <I18nProvider>
          <Shell>{children}</Shell>
        </I18nProvider>
      </body>
    </html>
  );
}
