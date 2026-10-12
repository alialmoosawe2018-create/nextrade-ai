"use client";

import Link from "next/link";
import { Suspense, useState } from "react";

import { Alert, Badge, Button, Card, Field, Num, Row, SourceBadge } from "@/components/ui";
import { api, ApiError, usePoll, type Connection, type RunRow } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

type RStats = { n: number; expectancy_r: number; sd_r: number | null; se_r: number | null; ci95_r: [number, number] | null };
type Summary = {
  WARNING?: string;
  status: string;
  trades: number;
  sessions_covered?: number;
  win_rate?: number;
  net_pnl?: number;
  profit_factor?: number | null;
  max_drawdown_pct?: number;
  ambiguous_bar_trades?: number;
  exit_reasons?: Record<string, number>;
  r?: RStats;
  cost_sensitivity?: Record<string, { trades: number; expectancy_r: number | null }>;
  note?: string;
};
type Result = { run_id: string; report_url: string; summary: Summary; quality: { verdict: string; first_bar: string | null; missing_minutes_total: number; sessions_partial: number }; server_tz: { kind: string; hours: number; zone: string | null; evidence: string }; data_source: string; sessions: number };

type Job = { job_id: string; status: "RUNNING" | "DONE" | "FAILED"; result: Result | null; error: { code?: string; message?: string; explanation?: string } | null };

function isoDaysAgo(n: number) {
  const d = new Date(Date.now() - n * 86400000);
  return d.toISOString().slice(0, 10);
}

// Default dates depend on "today", so they are computed per visit (inside Suspense), never at build time.
export default function AnalysisPage() {
  return (
    <Suspense fallback={null}>
      <Analysis />
    </Suspense>
  );
}

function Analysis() {
  const { t, lang } = useI18n();
  const { data: conn } = usePoll<Connection>("/api/connection/status", 10000);
  const { data: runs, reload } = usePoll<RunRow[]>("/api/analysis/runs", 30000);
  const [start, setStart] = useState(isoDaysAgo(365));
  const [end, setEnd] = useState(isoDaysAgo(1));
  const [split, setSplit] = useState("dev");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState<{ code?: string; message: string; detail?: unknown } | null>(null);

  async function run() {
    setBusy(true);
    setErr(null);
    setRes(null);
    setElapsed(0);
    const t0 = performance.now();
    try {
      const { job_id } = await api<{ job_id: string }>("/api/analysis/run", { method: "POST", json: { start, end, split } });
      // History download can take minutes on a real terminal: the job runs in the background and we poll it.
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        setElapsed(Math.round((performance.now() - t0) / 1000));
        const job = await api<Job>(`/api/analysis/jobs/${job_id}`);
        if (job.status === "DONE" && job.result) {
          setRes(job.result);
          break;
        }
        if (job.status === "FAILED") {
          const e = job.error ?? {};
          setErr({ code: e.code, message: e.message ?? e.explanation ?? "", detail: e });
          break;
        }
      }
      reload();
    } catch (e) {
      setErr(e instanceof ApiError ? { code: e.code, message: e.message, detail: e.detail } : { message: String(e) });
    } finally {
      setBusy(false);
    }
  }

  const ready = conn?.connected && conn.symbol;
  const scores = (err?.detail as { scores?: { rule: string; score: number; days: number }[] } | undefined)?.scores;

  return (
    <div className="space-y-4">
      {!ready && <Alert>{conn?.connected ? reasonLabel("NO_SYMBOL", lang) : t("goAccounts")}</Alert>}
      <Card title={t("navAnalysis")} actions={conn?.connected ? <SourceBadge source={conn.source} /> : null}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t("from")}><input type="date" className="num" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label={t("to")}><input type="date" className="num" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label={t("split")}>
            <select value={split} onChange={(e) => setSplit(e.target.value)}>
              <option value="dev">dev</option>
              <option value="validation">validation</option>
              <option value="holdout">holdout ⚠</option>
              <option value="all">all</option>
            </select>
          </Field>
        </div>
        <p className="mt-2 text-xs text-muted">{t("splitHelp")}</p>
        <div className="mt-3"><Button onClick={run} disabled={!ready || busy}>{busy ? `${t("running")} ${elapsed}s` : t("run")}</Button></div>
      </Card>

      {err && (
        <Alert tone="bad">
          <b>{err.code ? reasonLabel(err.code, lang) : ""}</b> {err.message}
          {err.code === "SPREAD_LIMIT_NOT_CONFIGURED" && <> — <Link className="underline" href="/settings">{t("navSettings")}</Link></>}
          {scores && (
            <table className="mt-2 text-xs"><tbody>{scores.map((s) => <tr key={s.rule}><td className="num pe-3">{s.rule}</td><td className="num pe-3">{Math.round(s.score * 100)}%</td><td className="num">{s.days}</td></tr>)}</tbody></table>
          )}
        </Alert>
      )}

      {res && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title={t("navAnalysis")} actions={<span className="flex gap-1"><SourceBadge source={res.data_source} /><Badge tone="warn">SIMULATED</Badge></span>}>
            {res.summary.WARNING && <div className="mb-2"><Alert tone="bad">{res.summary.WARNING}</Alert></div>}
            <Row k="status" v={<Badge tone={res.summary.status === "OK" ? "good" : "warn"}>{res.summary.status}</Badge>} />
            {res.summary.note && <p className="text-sm text-muted">{res.summary.note}</p>}
            <Row k={t("trades")} v={<span className="num">{res.summary.trades}</span>} />
            {res.summary.r && (
              <>
                <Row k="E[R]" v={<span><Num v={res.summary.r.expectancy_r} d={3} /> {res.summary.r.ci95_r && <span className="text-xs text-muted">(95%: <Num v={res.summary.r.ci95_r[0]} d={3} /> … <Num v={res.summary.r.ci95_r[1]} d={3} />)</span>}</span>} />
                <Row k="win rate" v={<Num v={(res.summary.win_rate ?? 0) * 100} d={1} />} />
                <Row k="profit factor" v={<Num v={res.summary.profit_factor} />} />
                <Row k="max DD %" v={<Num v={res.summary.max_drawdown_pct} />} />
                <Row k="ambiguous bars" v={<span className="num">{res.summary.ambiguous_bar_trades}</span>} />
              </>
            )}
            <a className="mt-3 inline-block text-accent underline" href={res.report_url} target="_blank" rel="noopener noreferrer">{t("report")}</a>
          </Card>
          <div className="space-y-4">
            {res.summary.cost_sensitivity && (
              <Card title={t("costSensitivity")}>
                {Object.entries(res.summary.cost_sensitivity).map(([k, v]) => <Row key={k} k={<span className="num">{k}</span>} v={<span><span className="num">{v.trades ?? 0}</span> · <Num v={v.expectancy_r} d={3} /> R</span>} />)}
              </Card>
            )}
            <Card title={t("quality")}>
              <Row k="verdict" v={<Badge tone={res.quality.verdict === "OK" ? "good" : "warn"}>{res.quality.verdict}</Badge>} />
              <Row k="first bar" v={<span className="num">{res.quality.first_bar ?? "—"}</span>} />
              <Row k="missing min" v={<span className="num">{res.quality.missing_minutes_total}</span>} />
              <Row k={t("serverTime")} v={<span className="text-xs">{res.server_tz.evidence}</span>} />
            </Card>
          </div>
        </div>
      )}

      <Card title={t("recentRuns")}>
        {runs && runs.length ? (
          <div className="overflow-x-auto"><table className="w-full text-sm"><tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className="border-b border-line/60">
                <td className="num py-1 text-xs text-muted">{r.created_utc}</td>
                <td className="py-1"><SourceBadge source={r.data_source} /></td>
                <td className="py-1">{r.split ?? "—"}</td>
                <td className="py-1"><Badge tone={r.status === "OK" ? "good" : "warn"}>{r.status}</Badge></td>
                <td className="num py-1">{r.trades ?? 0}</td>
                <td className="py-1"><Num v={r.expectancy_r} d={3} /> R</td>
                <td className="py-1 text-end"><a className="text-accent underline" href={r.report_url} target="_blank" rel="noopener noreferrer">{t("report")}</a></td>
              </tr>
            ))}
          </tbody></table></div>
        ) : <p className="text-sm text-muted">{t("none")}</p>}
      </Card>
    </div>
  );
}
