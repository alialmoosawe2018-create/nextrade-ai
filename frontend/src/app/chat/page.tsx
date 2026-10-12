"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, Badge, Button, Card, Num, SourceBadge } from "@/components/ui";
import { api, ApiError, usePoll, type Connection } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

type ActionResult = { type: string; ok: boolean; code?: string; detail?: unknown; asset?: string; side?: string };
type Msg = { id: string; role: "user" | "assistant"; text: string; at: string; actions?: ActionResult[]; deterministic?: boolean; data_source?: string };
type Job = { job_id: string | null; status: "RUNNING" | "DONE" | "FAILED"; messages: Msg[]; error?: { code?: string; message?: string } | null };
type Idea = {
  id: string; symbol: string; side: "BUY" | "SELL"; entry_ref: number; sl: number; tp: number | null; volume: number;
  risk_money: number; loss_at_sl: number; reward_risk: number | null; spread: number; rationale: string;
  data_source: string; account_mode: string; created_at: string; valid_until: string; status: string; execution: string;
};
type ChatStatus = { cli_found: boolean; model: string; note: string };

const QUICK = ["حلل US100", "حلل الذهب والنفط", "شغّل المراقبة الحية", "شنو وضع السوق هسه؟", "وقف"];

export default function Chat() {
  const { t, lang } = useI18n();
  const { data: conn } = usePoll<Connection>("/api/connection/status", 10000);
  const { data: status } = usePoll<ChatStatus>("/api/chat/status", 60000);
  const { data: history, reload } = usePoll<Msg[]>("/api/chat/history", 20000);
  const { data: ideas, reload: reloadIdeas } = usePoll<Idea[]>("/api/ideas", 10000);
  const [pending, setPending] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState<{ code?: string; message: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const messages = [...(history ?? []), ...pending.filter((p) => !(history ?? []).some((h) => h.id === p.id))];

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy]);

  async function send(message: string) {
    const m = message.trim();
    if (!m || busy) return;
    setBusy(true);
    setErr(null);
    setText("");
    try {
      let job = await api<Job>("/api/chat", { method: "POST", json: { message: m } });
      setPending(job.messages);
      while (job.status === "RUNNING" && job.job_id) {
        await new Promise((r) => setTimeout(r, 1200));
        job = await api<Job>(`/api/chat/jobs/${job.job_id}`);
      }
      if (job.status === "FAILED") setErr({ code: job.error?.code, message: job.error?.message ?? "" });
      setPending(job.messages);
      reload();
      reloadIdeas();
    } catch (e) {
      setErr(e instanceof ApiError ? { code: e.code, message: e.message } : { message: String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function dismiss(id: string) {
    await api(`/api/ideas/${id}/dismiss`, { method: "POST" }).catch(() => {});
    reloadIdeas();
  }

  const live = ideas?.filter((i) => i.status === "PROPOSED") ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="flex min-h-[70vh] flex-col lg:col-span-2">
        <Card title={<span className="flex items-center gap-2">{t("navChat")} {conn?.connected && <SourceBadge source={conn.source} />}</span>}
          actions={<button className="text-xs text-muted underline" onClick={() => api("/api/chat/clear", { method: "POST" }).then(() => { setPending([]); reload(); })}>{t("clearChat")}</button>}>
          <Alert tone="accent">{t("chatNoOrders")}</Alert>
          {status && !status.cli_found && <div className="mt-2"><Alert tone="bad">{reasonLabel("CLAUDE_CLI_MISSING", lang)}</Alert></div>}
          <div className="mt-3 max-h-[55vh] space-y-3 overflow-y-auto pe-1">
            {messages.length === 0 && <p className="text-sm text-muted">{t("chatEmpty")}</p>}
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
                <div className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-sm ${m.role === "user" ? "bg-accent/15" : "border border-line bg-panel-2"}`}>
                  {m.text}
                  {m.actions && m.actions.length > 0 && (
                    <div className="mt-2 space-y-1 border-t border-line/60 pt-2 text-xs">
                      {m.actions.map((a, i) => (
                        <div key={i} className="flex flex-wrap items-center gap-1">
                          <Badge tone={a.ok ? "good" : "bad"}>{a.ok ? "✓" : "✗"} {a.type}</Badge>
                          {!a.ok && <span className="text-bad">{reasonLabel(a.code ?? "", lang)} — {String(a.detail ?? "")}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && <p className="text-end text-xs text-muted">{t("claudeThinking")}</p>}
            <div ref={bottom} />
          </div>
          {err && (
            <div className="mt-3">
              <Alert tone="bad">
                <b>{reasonLabel(err.code ?? "", lang)}</b> {err.message}
                {err.code === "CLAUDE_NOT_LOGGED_IN" && <p className="mt-1 text-xs">{t("claudeLoginHelp")}</p>}
              </Alert>
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {QUICK.map((q) => <button key={q} disabled={busy} onClick={() => send(q)} className="rounded-full border border-line px-3 py-1 text-xs hover:bg-panel-2 disabled:opacity-50">{q}</button>)}
          </div>
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder={t("chatPlaceholder")} maxLength={2000} disabled={busy} />
            <Button type="submit" disabled={busy || !text.trim()}>{t("send")}</Button>
          </form>
        </Card>
      </div>

      <div className="space-y-4">
        <Card title={t("tradeIdeas")}>
          <p className="mb-2 text-xs text-muted">{t("ideasManual")}</p>
          {live.length === 0 && <p className="text-sm text-muted">{t("none")}</p>}
          {live.map((i) => (
            <div key={i.id} className="mb-3 rounded-lg border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={i.side === "BUY" ? "good" : "bad"}>{i.side}</Badge>
                <span className="num font-semibold">{i.symbol}</span>
                <SourceBadge source={i.data_source} />
                <Badge tone={i.account_mode === "REAL" ? "warn" : "accent"}>{i.account_mode}</Badge>
              </div>
              <div className="num mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <span>Entry ≈ <Num v={i.entry_ref} d={i.entry_ref < 10 ? 5 : 2} /></span>
                <span>Lots <b>{i.volume}</b></span>
                <span className="text-bad">SL <Num v={i.sl} d={i.sl < 10 ? 5 : 2} /></span>
                <span className="text-good">TP {i.tp == null ? "—" : <Num v={i.tp} d={i.tp < 10 ? 5 : 2} />}</span>
                <span>Risk ≤ <Num v={i.loss_at_sl} /></span>
                <span>R:R {i.reward_risk ?? "—"}</span>
              </div>
              {i.rationale && <p className="mt-2 text-xs text-muted">{i.rationale}</p>}
              <p className="num mt-1 text-[11px] text-muted">valid until {i.valid_until.slice(11, 19)} UTC</p>
              <div className="mt-2"><Button variant="ghost" onClick={() => dismiss(i.id)}>{t("dismiss")}</Button></div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}
