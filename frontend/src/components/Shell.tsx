"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { usePoll, type Health } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";
import { usePref } from "@/lib/prefs";
import { Badge, SourceBadge } from "@/components/ui";

const NAV: { href: string; key: Key; icon: string }[] = [
  { href: "/", key: "navOverview", icon: "◧" },
  { href: "/accounts", key: "navAccounts", icon: "⚿" },
  { href: "/chat", key: "navChat", icon: "✦" },
  { href: "/live", key: "navLive", icon: "◉" },
  { href: "/analysis", key: "navAnalysis", icon: "▤" },
  { href: "/learning", key: "navLearning", icon: "◈" },
  { href: "/settings", key: "navSettings", icon: "⚙" },
];

function EmergencyStop() {
  const { t } = useI18n();
  const { data, reload } = usePoll<{ values: { risk: { trading_enabled: boolean } } }>("/api/settings", 5000);
  const on = data && !data.values.risk.trading_enabled;
  async function stop() {
    await fetch("/api/kill", { method: "POST" });
    reload();
  }
  async function release() {
    if (!window.confirm(t("confirmRelease"))) return;
    await fetch("/api/kill/release", { method: "POST" });
    reload();
  }
  return on ? (
    <button onClick={release} className="rounded-md border border-bad bg-bad/15 px-2 py-1 text-xs font-semibold text-bad">
      {t("killOn")} · {t("release")}
    </button>
  ) : (
    <button onClick={stop} className="rounded-md bg-bad px-2 py-1 text-xs font-semibold text-white">{t("emergencyStop")}</button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { t, lang, setLang } = useI18n();
  const path = usePathname();
  const { data: health, error } = usePoll<Health>("/api/health", 5000);
  const [theme, setTheme] = usePref<"dark" | "light">("nextrade.theme", ["dark", "light"], "dark");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const conn = health?.connection;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 border-e border-line bg-panel md:block">
        <div className="px-4 py-5">
          <div className="text-lg font-bold">{t("appName")}</div>
          <div className="text-xs text-muted">{t("tagline")}</div>
        </div>
        <nav className="space-y-1 px-2">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${active(n.href) ? "bg-accent/15 text-accent" : "text-text hover:bg-panel-2"}`}
            >
              <span aria-hidden>{n.icon}</span>
              {t(n.key)}
            </Link>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-line bg-bg/95 px-4 py-2 backdrop-blur">
          <span className="font-bold md:hidden">{t("appName")}</span>
          <Badge tone="accent">{t("modeSim")}</Badge>
          <Badge tone="good">🔒 {t("realLocked")}</Badge>
          {error ? (
            <Badge tone="bad">{t("backendDown")}</Badge>
          ) : conn?.connected ? (
            <>
              <Badge tone="good">● {t("connected")}</Badge>
              <SourceBadge source={conn.source} />
              {conn.symbol && <Badge>{conn.symbol}</Badge>}
            </>
          ) : (
            <Badge>○ {t("notConnected")}</Badge>
          )}
          <div className="ms-auto flex items-center gap-2">
            <EmergencyStop />
            <button className="rounded-md border border-line px-2 py-1 text-xs" onClick={() => setLang(lang === "ar" ? "en" : "ar")}>
              {lang === "ar" ? "English" : "العربية"}
            </button>
            <button className="rounded-md border border-line px-2 py-1 text-xs" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="theme">
              {theme === "dark" ? "☀" : "☾"}
            </button>
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-4 md:pb-8">{children}</main>

        <nav className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-7 border-t border-line bg-panel md:hidden">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={`flex flex-col items-center py-2 text-[11px] ${active(n.href) ? "text-accent" : "text-muted"}`}>
              <span aria-hidden className="text-base">{n.icon}</span>
              {t(n.key)}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}
