"use client";

import Link from "next/link";

import { Alert, Badge, Card, Num, Row, SourceBadge } from "@/components/ui";
import { usePoll, type Health, type LiveSnapshot, type RunRow } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

type WatchRow = { asset: string; name_ar: string; name_en: string; symbol: string | null; bid: number | null; ask: number | null; spread: number | null; source: string };

export default function Overview() {
  const { t, lang } = useI18n();
  const { data: health, error } = usePoll<Health>("/api/health", 5000);
  const { data: live } = usePoll<LiveSnapshot>("/api/live/status", 5000);
  const { data: runs } = usePoll<RunRow[]>("/api/analysis/runs", 15000);
  const conn = health?.connection;
  const { data: watch } = usePoll<WatchRow[]>(conn?.connected ? "/api/watchlist" : null, 5000);

  if (error) return <Alert tone="bad">{t("backendDown")}</Alert>;
  if (!health) return <p className="text-muted">{t("loading")}</p>;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title={t("connection")}>
        {conn?.connected ? (
          <>
            <Row k={t("account")} v={<span className="num">{conn.login ?? "—"}</span>} />
            <Row k={t("server")} v={<span className="num">{conn.server}</span>} />
            <Row k={t("company")} v={conn.company} />
            <Row k={t("accountMode")} v={<span className="flex gap-1"><Badge tone={conn.account_mode === "REAL" ? "warn" : "accent"}>{conn.account_mode}</Badge><SourceBadge source={conn.source} /></span>} />
            <Row k="" v={conn.read_only ? <Badge tone="good">{t("readOnly")}</Badge> : <Badge tone="warn">{t("canTrade")}</Badge>} />
            <Row k={t("symbol")} v={conn.symbol ?? <span className="text-muted">{t("none")}</span>} />
            <Row k={t("serverTime")} v={conn.server_tz ? <span className="num">{conn.server_tz.kind === "ny_shift" ? `NY${conn.server_tz.hours >= 0 ? "+" : ""}${conn.server_tz.hours}` : conn.server_tz.zone ?? `UTC+${conn.server_tz.hours}`}</span> : <span className="text-xs text-muted">{t("undetermined")}</span>} />
            {conn.source === "MOCK" && <div className="mt-2"><Alert tone="bad">{t("mockNote")}</Alert></div>}
            {conn.warnings.map((w) => <div key={w} className="mt-2"><Alert>{w}</Alert></div>)}
          </>
        ) : (
          <div className="space-y-2 text-sm">
            <p className="text-muted">{t("notConnected")}</p>
            <Link className="text-accent underline" href="/accounts">{t("goAccounts")}</Link>
          </div>
        )}
      </Card>

      <Card title={t("liveStatus")}>
        {live && live.status !== "OFF" ? (
          <>
            <Row k="" v={<Badge tone={live.status === "RUNNING" ? "good" : live.status.includes("ERROR") || live.status === "STALE_DATA" ? "bad" : "neutral"}>{live.status}</Badge>} />
            <Row k={t("phase")} v={live.phase ?? "—"} />
            <Row k={t("orRange")} v={<span className="flex gap-3"><span>↓ <Num v={live.or_low} /></span><span>↑ <Num v={live.or_high} /></span></span>} />
            <Row k={t("paperBalance")} v={<span><Num v={live.paper_balance} /> <Badge tone="warn">SIMULATED</Badge></span>} />
            <Row k={t("trades")} v={<span className="num">{live.trades?.length ?? 0}</span>} />
          </>
        ) : (
          <p className="text-sm text-muted">OFF</p>
        )}
        <p className="mt-3 text-xs text-muted">{t("simulatedNote")}</p>
      </Card>

      {watch && (
        <Card title={t("watchlist")} className="md:col-span-2" actions={<SourceBadge source={watch[0]?.source} />}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {watch.map((w) => (
              <div key={w.asset} className="rounded-lg border border-line p-2 text-sm">
                <div className="flex items-center justify-between gap-2"><span className="font-medium">{lang === "ar" ? w.name_ar : w.name_en}</span><span className="num text-xs text-muted">{w.symbol ?? "—"}</span></div>
                {w.symbol ? (
                  <div className="num mt-1 text-xs">
                    <Num v={w.bid} d={w.bid != null && w.bid < 10 ? 5 : 2} /> / <Num v={w.ask} d={w.ask != null && w.ask < 10 ? 5 : 2} />
                    <span className="ms-2 text-muted">sp <Num v={w.spread} d={w.spread != null && w.spread < 0.1 ? 5 : 2} /></span>
                  </div>
                ) : <div className="mt-1 text-xs text-muted">{lang === "ar" ? "غير متوفر عند الوسيط" : "not at this broker"}</div>}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title={t("recentRuns")} className="md:col-span-2">
        {runs && runs.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <tbody>
                {runs.slice(0, 6).map((r) => (
                  <tr key={r.run_id} className="border-b border-line/60">
                    <td className="py-1.5"><SourceBadge source={r.data_source} /></td>
                    <td className="py-1.5">{r.split ?? "—"}</td>
                    <td className="py-1.5">{r.symbol ?? "—"}</td>
                    <td className="py-1.5"><Badge tone={r.status === "OK" ? "good" : "warn"}>{r.status}</Badge></td>
                    <td className="num py-1.5">{r.trades ?? 0}</td>
                    <td className="py-1.5"><Num v={r.expectancy_r} /> R</td>
                    <td className="py-1.5 text-end"><a className="text-accent underline" href={r.report_url} target="_blank" rel="noopener noreferrer">{t("report")}</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">{t("none")}</p>
        )}
      </Card>

      {health.recent_errors.length > 0 && (
        <Card title={t("recentErrors")} className="md:col-span-2">
          {health.recent_errors.slice(-5).reverse().map((e) => (
            <p key={e.at + e.error} className="text-xs"><span className="num text-muted">{e.at}</span> · {e.where}: {reasonLabel(e.error, lang)}</p>
          ))}
        </Card>
      )}
    </div>
  );
}
