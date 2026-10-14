"use client";

import { useState } from "react";

import { Alert, Badge, Button, Card, Num, Row } from "@/components/ui";
import { api, ApiError, usePoll } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

type Stats = { n: number; mean_r: number | null; se: number | null };
type Eval = { skipped: Stats; kept: Stats; t: number | null; total_r_skipped: number };
type Lesson = {
  id: string; symbol: string; feature: string; op: string; value: number; status: string; run_id: string;
  evidence: { discovery?: Eval; validation?: Eval; all?: Eval; trades_total?: number; rules_tested?: number };
};
type Summary = {
  symbol: string | null; latest_run: string | null; backtest_trades: number; paper_trades: number; lessons: Lesson[];
  ideas: { resolved: number; win_rate?: number; mean_r?: number; total_r?: number; warning?: string | null; note?: string };
  feature_docs: Record<string, string>; adopted_filters: { id: string; feature: string; op: string; value: number; note: string }[];
};
type Loss = {
  uid: string; entry_time: string; direction: string; exit_reason: string; r: number; flags: string[];
  review: { verdict: string; factors: string[]; explanation: string } | null;
};
type Losses = { run_id: string | null; trades?: number; losing?: number; losses: Loss[]; note?: string };
type Idea = { id: string; symbol: string; side: string; entry_ref: number; sl: number; tp: number | null; created_at: string; outcome: string | null; outcome_r: number | null };

const OP = { gt: ">", lt: "<", eq: "=" } as Record<string, string>;
const VERDICT_TONE = { VARIANCE: "neutral", PROCESS: "warn", UNCLEAR: "neutral" } as const;

export default function Learning() {
  const { t, lang } = useI18n();
  const { data: sum, reload } = usePoll<Summary>("/api/learning/summary", 20000);
  const { data: losses, reload: reloadLosses } = usePoll<Losses>(sum?.symbol ? "/api/learning/losses" : null, 60000);
  const { data: ideas } = usePoll<{ ideas: Idea[] }>("/api/learning/ideas", 60000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "good" | "bad" | "accent"; text: string } | null>(null);

  async function act(key: string, fn: () => Promise<string>) {
    setBusy(key);
    setMsg(null);
    try {
      setMsg({ tone: "good", text: await fn() });
      reload();
      reloadLosses();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof ApiError ? `${reasonLabel(e.code ?? "", lang)} ${e.message}` : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const discoverFn = async () => {
    const r = await api<{ status: string; trades: number; rules_tested?: number; lessons: Lesson[]; note?: string }>("/api/learning/discover", { method: "POST" });
    if (r.status !== "OK") return `${r.status} — ${r.note ?? ""}`;
    const v = r.lessons.filter((l) => l.status === "VALIDATED").length;
    return `${t("discoverDone")}: ${r.trades} · ${r.rules_tested} ${t("rulesTested")} · ${v} ${t("validated")}`;
  };

  const reviewFn = async () => {
    const r = await api<{ job_id: string | null; count?: number; note?: string }>("/api/learning/review", { method: "POST" });
    if (!r.job_id) return r.note ?? "";
    for (;;) {
      await new Promise((res) => setTimeout(res, 1500));
      const j = await api<{ status: string; reviewed: number; error?: { code?: string; message?: string } | null }>(`/api/learning/review/${r.job_id}`);
      if (j.status === "DONE") return `${t("reviewed")}: ${j.reviewed}`;
      if (j.status === "FAILED") throw new ApiError(409, j.error?.message ?? "", j.error?.code);
    }
  };

  const open = sum?.lessons.filter((l) => ["VALIDATED", "UNVALIDATED"].includes(l.status)) ?? [];

  return (
    <div className="space-y-4">
      <Alert tone="accent">{t("learningIntro")}</Alert>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t("ideasTrack")}>
          {sum?.ideas.resolved ? (
            <>
              <Row k={t("resolvedIdeas")} v={<span className="num">{sum.ideas.resolved}</span>} />
              <Row k="win rate" v={<Num v={(sum.ideas.win_rate ?? 0) * 100} d={1} />} />
              <Row k="E[R]" v={<Num v={sum.ideas.mean_r} d={3} />} />
              {sum.ideas.warning && <div className="mt-2"><Alert>{sum.ideas.warning}</Alert></div>}
            </>
          ) : <p className="text-sm text-muted">{t("noResolvedIdeas")}</p>}
          <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-xs">
            {ideas?.ideas.slice(0, 20).map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2">
                <Badge tone={i.side === "BUY" ? "good" : "bad"}>{i.side}</Badge>
                <span className="num">{i.symbol}</span>
                <span className="num text-muted">{i.created_at.slice(5, 16).replace("T", " ")}</span>
                <Badge tone={i.outcome === "TARGET" ? "good" : i.outcome === "STOP" ? "bad" : "neutral"}>{i.outcome ?? "OPEN"}</Badge>
                {i.outcome_r != null && <span className="num"><Num v={i.outcome_r} /> R</span>}
              </li>
            ))}
          </ul>
        </Card>

        <Card title={t("adoptedRules")}>
          {sum?.adopted_filters.length ? sum.adopted_filters.map((f) => (
            <div key={f.id} className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="num">{f.feature} {OP[f.op]} {f.value}</span>
              <Button variant="ghost" disabled={!!busy} onClick={() => act(f.id, async () => { await api(`/api/learning/lessons/${f.id}/retire`, { method: "POST" }); return t("retired"); })}>{t("retire")}</Button>
            </div>
          )) : <p className="text-sm text-muted">{t("none")}</p>}
          <p className="mt-2 text-xs text-muted">{t("adoptHint")}</p>
        </Card>
      </div>

      <Card title={t("lessons")} actions={<Button disabled={!!busy || !sum?.latest_run} onClick={() => act("discover", discoverFn)}>{busy === "discover" ? t("loading") : t("discover")}</Button>}>
        {!sum?.latest_run && <Alert>{t("needAnalysis")}</Alert>}
        <p className="mb-3 text-xs text-muted">{t("protocol")}</p>
        {open.length === 0 && sum?.latest_run && <p className="text-sm text-muted">{t("noLessons")}</p>}
        <div className="space-y-3">
          {open.map((l) => {
            const d = l.evidence.discovery, v = l.evidence.validation;
            return (
              <div key={l.id} className="rounded-lg border border-line p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={l.status === "VALIDATED" ? "good" : "neutral"}>{l.status}</Badge>
                  <span className="font-medium">{t("skipWhen")} <span className="num">{l.feature} {OP[l.op]} {l.value}</span></span>
                </div>
                <p className="mt-1 text-xs text-muted">{sum?.feature_docs[l.feature]}</p>
                <div className="num mt-2 grid gap-1 text-xs sm:grid-cols-2">
                  {d && <span>{t("discovery")}: {t("skipped")} {d.skipped.n} @ <Num v={d.skipped.mean_r} d={2} />R · {t("kept")} {d.kept.n} @ <Num v={d.kept.mean_r} d={2} />R · t={d.t}</span>}
                  {v && <span>{t("validation")}: {t("skipped")} {v.skipped.n} @ <Num v={v.skipped.mean_r} d={2} />R · {t("kept")} {v.kept.n} @ <Num v={v.kept.mean_r} d={2} />R</span>}
                </div>
                <div className="mt-2 flex gap-2">
                  <Button disabled={!!busy || l.status !== "VALIDATED"} onClick={() => act(l.id, async () => { await api(`/api/learning/lessons/${l.id}/approve`, { method: "POST" }); return t("adopted"); })}>{t("approve")}</Button>
                  <Button variant="ghost" disabled={!!busy} onClick={() => act(l.id, async () => { await api(`/api/learning/lessons/${l.id}/reject`, { method: "POST" }); return t("rejected"); })}>{t("reject")}</Button>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title={t("losingTrades")} actions={<Button variant="ghost" disabled={!!busy || !losses?.losses.length} onClick={() => act("review", reviewFn)}>{busy === "review" ? t("claudeThinking") : t("askReview")}</Button>}>
        {losses?.note && <p className="text-sm text-muted">{losses.note}</p>}
        {losses?.losing != null && <p className="mb-2 text-xs text-muted"><span className="num">{losses.losing}/{losses.trades}</span> {t("losingOf")}</p>}
        <div className="space-y-2">
          {losses?.losses.map((x) => (
            <div key={x.uid} className="rounded-lg border border-line/70 p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="num text-muted">{x.entry_time.slice(0, 16)}</span>
                <Badge tone={x.direction === "LONG" ? "good" : "bad"}>{x.direction}</Badge>
                <span className="num"><Num v={x.r} /> R</span>
                <span>{reasonLabel(x.exit_reason, lang)}</span>
                {x.review && <Badge tone={VERDICT_TONE[x.review.verdict as keyof typeof VERDICT_TONE] ?? "neutral"}>{x.review.verdict}</Badge>}
              </div>
              {x.flags.length > 0 && <p className="num mt-1 text-muted">{x.flags.join(" · ")}</p>}
              {x.review && <p className="mt-1">{t("hypothesis")}: {x.review.explanation}</p>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
