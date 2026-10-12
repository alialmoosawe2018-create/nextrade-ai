"use client";

import { useMemo, useState } from "react";

import { CandleChart, type Candle, type Level } from "@/components/CandleChart";
import { Alert, Badge, Button, Card, Num, Row, SourceBadge } from "@/components/ui";
import { api, ApiError, usePoll, type Connection, type LiveSnapshot } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

type Quote = { available: boolean; bid?: number; ask?: number; spread?: number; measured_server_offset_hours?: number | null; source?: string };
type CandleResp = { day: string; source?: string; session: { open_utc: string; close_utc: string; open_baghdad: string; early_close: boolean } | null; candles: Candle[] };

const fmtTime = (iso?: string | null, tz = "Asia/Baghdad") =>
  iso ? new Date(iso.replace(" ", "T")).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" }) : "—";

export default function Live() {
  const { t, lang } = useI18n();
  const { data: conn } = usePoll<Connection>("/api/connection/status", 5000);
  const ready = conn?.connected && !!conn.symbol;
  const { data: live, reload } = usePoll<LiveSnapshot>("/api/live/status", 2000);
  const { data: quote } = usePoll<Quote>(ready ? "/api/market/quote" : null, 3000);
  const { data: chart, error: chartErr } = usePoll<CandleResp>(ready && conn?.connected && conn.server_tz ? "/api/market/candles" : null, 10000);
  const [err, setErr] = useState<string | null>(null);
  const running = live && !["OFF", "STOPPED"].includes(live.status);

  const levels = useMemo<Level[]>(() => {
    const out: Level[] = [];
    if (live?.or_high != null) out.push({ price: live.or_high, title: "OR H", color: "#4f8cff" });
    if (live?.or_low != null) out.push({ price: live.or_low, title: "OR L", color: "#4f8cff" });
    for (const p of live?.open_positions ?? []) {
      out.push({ price: p.stop, title: "STOP", color: "#f46a6a" }, { price: p.target, title: "TP", color: "#34c38f" });
    }
    return out;
  }, [live]);

  async function act(path: string) {
    setErr(null);
    try {
      await api(path, { method: "POST" });
      reload();
    } catch (e) {
      setErr(e instanceof ApiError ? `${reasonLabel(e.code ?? "", lang)} — ${e.message}` : String(e));
    }
  }

  if (!conn?.connected) return <Alert>{t("goAccounts")}</Alert>;
  if (!conn.symbol) return <Alert>{reasonLabel("NO_SYMBOL", lang)}</Alert>;

  return (
    <div className="space-y-4">
      <Alert tone={conn.source === "MOCK" ? "bad" : "accent"}>{conn.source === "MOCK" ? t("mockNote") : t("simulatedNote")}</Alert>
      <div className="flex flex-wrap items-center gap-2">
        {running ? <Button variant="danger" onClick={() => act("/api/live/stop")}>{t("stop")}</Button> : <Button onClick={() => act("/api/live/start")}>{t("start")}</Button>}
        {live && <Badge tone={live.status === "RUNNING" ? "good" : live.status === "STALE_DATA" || live.status.includes("ERROR") ? "bad" : "neutral"}>{live.status}</Badge>}
        <SourceBadge source={live?.data_source ?? conn.source} />
        {live?.message && <span className="text-xs text-muted">{live.message}</span>}
      </div>
      {err && <Alert tone="bad">{err}</Alert>}
      {live?.error && <Alert tone="bad">{live.error}</Alert>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={t("session")}>
          <Row k={t("nyOpen")} v={<span className="num">{fmtTime(live?.session?.open_utc ?? chart?.session?.open_utc, "America/New_York")}</span>} />
          <Row k={t("baghdadTime")} v={<span className="num">{fmtTime(live?.session?.open_utc ?? chart?.session?.open_utc)}</span>} />
          <Row k={t("phase")} v={live?.phase ?? "—"} />
          <Row k={t("orRange")} v={<span className="flex gap-3"><span>↓ <Num v={live?.or_low} /></span><span>↑ <Num v={live?.or_high} /></span></span>} />
          <Row k={t("direction")} v={live?.direction ?? "—"} />
          <Row k={t("dataAge")} v={live?.data_age_seconds != null ? <span className="num">{Math.round(live.data_age_seconds)}s</span> : "—"} />
        </Card>
        <Card title={t("quote")}>
          {quote?.available ? (
            <>
              <Row k="Bid" v={<Num v={quote.bid} />} />
              <Row k="Ask" v={<Num v={quote.ask} />} />
              <Row k={t("spread")} v={<Num v={quote.spread} />} />
              <Row k={t("serverTime")} v={quote.measured_server_offset_hours != null ? <span className="num">UTC{quote.measured_server_offset_hours >= 0 ? "+" : ""}{quote.measured_server_offset_hours}</span> : "—"} />
            </>
          ) : (
            <p className="text-sm text-muted">—</p>
          )}
        </Card>
        <Card title={t("paperBalance")}>
          <div className="text-2xl"><Num v={live?.paper_balance} /></div>
          <Badge tone="warn">SIMULATED</Badge>
          <p className="mt-2 text-xs text-muted">{t("simulatedNote")}</p>
        </Card>
      </div>

      <Card title={t("todayChart")} actions={<span className="text-xs text-muted">UTC</span>}>
        {!conn.server_tz ? <Alert>{reasonLabel("SERVER_TIME_UNDETERMINED", lang)} — {t("undetermined")}</Alert>
          : chartErr ? <Alert tone="bad">{chartErr.message}</Alert>
          : chart && chart.candles.length === 0 ? <p className="text-sm text-muted">{t("none")}</p>
          : <CandleChart candles={chart?.candles ?? []} levels={levels} />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t("openPositions")}>
          {(live?.open_positions?.length ?? 0) === 0 ? <p className="text-sm text-muted">{t("none")}</p> : live!.open_positions!.map((p) => (
            <div key={p.entry_time} className="text-sm">
              <Badge tone={p.direction === "LONG" ? "good" : "bad"}>{p.direction}</Badge> <Num v={p.entry_price} /> · SL <Num v={p.stop} /> · TP <Num v={p.target} /> · <span className="num">{p.volume}</span>
            </div>
          ))}
          <h3 className="mb-1 mt-4 text-sm text-muted">{t("trades")}</h3>
          {(live?.trades?.length ?? 0) === 0 ? <p className="text-sm text-muted">{t("none")}</p> : (
            <table className="w-full text-sm"><tbody>
              {live!.trades!.map((tr) => (
                <tr key={tr.candidate_id + tr.exit_time} className="border-b border-line/60">
                  <td className="py-1"><Badge tone={tr.direction === "LONG" ? "good" : "bad"}>{tr.direction}</Badge></td>
                  <td className="num py-1">{fmtTime(tr.entry_time)}</td>
                  <td className="py-1">{reasonLabel(tr.exit_reason, lang)}</td>
                  <td className="py-1"><Num v={tr.r_multiple} /> R</td>
                  <td className="py-1"><Num v={tr.net_pnl} /></td>
                </tr>
              ))}
            </tbody></table>
          )}
        </Card>
        <Card title={t("events")}>
          <ul className="max-h-80 space-y-1 overflow-y-auto text-xs">
            {(live?.events ?? []).slice().reverse().map((e, i) => (
              <li key={i} className="border-b border-line/50 pb-1">
                <span className="num text-muted">{fmtTime(e.time)}</span> · <Badge>{e.stage}</Badge> <b>{e.status}</b>{" "}
                {e.reasons.map((r) => reasonLabel(r, lang)).join("، ")}
              </li>
            ))}
            {(live?.events?.length ?? 0) === 0 && <li className="text-muted">{t("none")}</li>}
          </ul>
        </Card>
      </div>
    </div>
  );
}
