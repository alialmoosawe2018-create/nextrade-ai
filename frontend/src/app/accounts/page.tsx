"use client";

import { useLayoutEffect, useState } from "react";

import { Alert, Badge, Button, Card, Field, Row, SourceBadge } from "@/components/ui";
import { api, ApiError, usePoll, type Connection, type Profile, type Spec, type SymbolRow } from "@/lib/api";
import { reasonLabel, useI18n } from "@/lib/i18n";

export default function Accounts() {
  const { t, lang } = useI18n();
  const { data: profiles, reload: reloadProfiles } = usePoll<Profile[]>("/api/accounts", 30000);
  const { data: conn, reload: reloadConn } = usePoll<Connection>("/api/connection/status", 5000);
  const [form, setForm] = useState({ label: "", server: "", login: "", terminal_path: "" });
  const [connectFor, setConnectFor] = useState<Profile | null>(null);
  const [method, setMethod] = useState<"credentials" | "attach">("credentials");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "bad" | "good"; text: string } | null>(null);

  // Pages stay mounted (hidden) after navigating away: wipe the password the moment we are hidden.
  useLayoutEffect(() => () => {
    setPassword("");
    setConnectFor(null);
  }, []);

  const fail = (e: unknown) => setMsg({ tone: "bad", text: e instanceof ApiError ? `${reasonLabel(e.code ?? "", lang) !== (e.code ?? "") ? reasonLabel(e.code ?? "", lang) + " — " : ""}${e.message}` : String(e) });

  async function addProfile(kind: "MT5" | "MOCK") {
    setMsg(null);
    try {
      const body = kind === "MOCK"
        ? { label: t("mockBroker"), kind: "MOCK" }
        : { label: form.label, kind: "MT5", server: form.server.trim(), login: form.login ? Number(form.login) : null, terminal_path: form.terminal_path.trim() || null };
      await api("/api/accounts", { method: "POST", json: body });
      setForm({ label: "", server: "", login: "", terminal_path: "" });
      reloadProfiles();
    } catch (e) {
      fail(e);
    }
  }

  async function connect() {
    if (!connectFor) return;
    setBusy(true);
    setMsg(null);
    const pw = password;
    setPassword(""); // never keep it in state longer than the request
    try {
      const body = connectFor.kind === "MOCK" ? { profile_id: connectFor.id } : { profile_id: connectFor.id, method, password: method === "credentials" ? pw : null };
      await api("/api/connection/connect", { method: "POST", json: body });
      setConnectFor(null);
      setMsg({ tone: "good", text: t("connected") });
      reloadConn();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    await api("/api/connection/disconnect", { method: "POST" }).catch(fail);
    reloadConn();
  }

  async function remove(id: string) {
    await api(`/api/accounts/${id}`, { method: "DELETE" }).catch(fail);
    reloadProfiles();
  }

  const connected = conn?.connected ? conn : null;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card title={t("navAccounts")}>
          {profiles && profiles.length === 0 && <p className="text-sm text-muted">{t("none")}</p>}
          <ul className="space-y-2">
            {profiles?.map((p) => {
              const isConnected = connected?.profile_id === p.id;
              return (
                <li key={p.id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.label}</span>
                    {p.kind === "MOCK" && <Badge tone="bad">MOCK</Badge>}
                    {isConnected && <Badge tone="good">● {t("connected")}</Badge>}
                  </div>
                  <div className="num mt-1 text-xs text-muted">{p.server} {p.login ? `· ${p.login}` : ""}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {isConnected ? (
                      <Button variant="ghost" onClick={disconnect}>{t("disconnect")}</Button>
                    ) : (
                      <Button onClick={() => { setConnectFor(p); setMsg(null); }}>{t("connect")}</Button>
                    )}
                    {!isConnected && <Button variant="danger" onClick={() => remove(p.id)}>{t("delete")}</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>

        {connectFor && (
          <Card title={`${t("connect")}: ${connectFor.label}`}>
            {connectFor.kind === "MOCK" ? (
              <Alert tone="bad">{t("mockNote")}</Alert>
            ) : (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); connect(); }} autoComplete="off">
                <fieldset className="space-y-1 text-sm">
                  <legend className="mb-1 text-muted">{t("method")}</legend>
                  <label className="flex items-center gap-2"><input type="radio" checked={method === "credentials"} onChange={() => setMethod("credentials")} /> {t("methodCredentials")}</label>
                  <label className="flex items-center gap-2"><input type="radio" checked={method === "attach"} onChange={() => setMethod("attach")} /> {t("methodAttach")}</label>
                </fieldset>
                {method === "credentials" && (
                  <Field label={t("password")} hint={t("investorAdvice")}>
                    <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" name="mt5-pass" required />
                  </Field>
                )}
                <p className="text-xs text-muted">🔒 {t("passwordNeverSaved")}</p>
              </form>
            )}
            <div className="mt-3 flex gap-2">
              <Button onClick={connect} disabled={busy || (connectFor.kind === "MT5" && method === "credentials" && !password)}>{busy ? t("loading") : t("connect")}</Button>
              <Button variant="ghost" onClick={() => { setConnectFor(null); setPassword(""); }}>{t("cancel")}</Button>
            </div>
          </Card>
        )}

        {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

        <Card title={t("addAccount")}>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); addProfile("MT5"); }}>
            <Field label={t("label")}><input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} required maxLength={60} /></Field>
            <Field label={t("server")} hint={t("serverHint")}><input className="num" value={form.server} onChange={(e) => setForm({ ...form, server: e.target.value })} required /></Field>
            <Field label={t("login")}><input className="num" inputMode="numeric" pattern="[0-9]*" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} required /></Field>
            <Field label={t("terminalPath")} hint={t("terminalPathHint")}><input className="num" value={form.terminal_path} onChange={(e) => setForm({ ...form, terminal_path: e.target.value })} /></Field>
            <p className="text-xs text-muted">🔒 {t("passwordNeverSaved")}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="submit">{t("addAccount")}</Button>
              <Button variant="ghost" onClick={() => addProfile("MOCK")}>{t("addMock")}</Button>
            </div>
          </form>
        </Card>
      </div>

      <div className="space-y-4">{connected ? <ConnectedPanel conn={connected} onChange={reloadConn} /> : <Card title={t("connection")}><p className="text-sm text-muted">{t("notConnected")}</p></Card>}</div>
    </div>
  );
}

function ConnectedPanel({ conn, onChange }: { conn: Extract<Connection, { connected: true }>; onChange: () => void }) {
  const { t, lang } = useI18n();
  const [all, setAll] = useState(false);
  const { data: symbols, error } = usePoll<SymbolRow[]>(`/api/symbols?nasdaq_only=${!all}`, 60000);
  const { data: inst, reload } = usePoll<{ spec: Spec | null; raw?: Record<string, unknown>; missing?: string[] }>("/api/instrument", 30000);
  const [err, setErr] = useState<string | null>(null);

  async function choose(name: string) {
    setErr(null);
    try {
      await api("/api/instrument/select", { method: "POST", json: { symbol: name } });
      reload();
      onChange();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <Card title={t("connection")} actions={<SourceBadge source={conn.source} />}>
        <Row k={t("company")} v={conn.company} />
        <Row k={t("server")} v={<span className="num">{conn.server}</span>} />
        <Row k={t("account")} v={<span className="num">{conn.login}</span>} />
        <Row k={t("accountMode")} v={<Badge tone={conn.account_mode === "REAL" ? "warn" : "accent"}>{conn.account_mode}</Badge>} />
        <Row k="" v={conn.read_only ? <Badge tone="good">{t("readOnly")}</Badge> : <Badge tone="warn">{t("canTrade")}</Badge>} />
        {conn.source === "MOCK" && <div className="mt-2"><Alert tone="bad">{t("mockNote")}</Alert></div>}
        {conn.warnings.map((w) => <div key={w} className="mt-2"><Alert>{w}</Alert></div>)}
      </Card>

      <Card title={t("chooseSymbol")} actions={<label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> {t("showAllSymbols")}</label>}>
        {error && <Alert tone="bad">{error.message}</Alert>}
        <ul className="max-h-64 space-y-1 overflow-y-auto">
          {symbols?.map((s) => (
            <li key={s.name} className="flex items-center justify-between gap-2 rounded-md px-2 py-1 hover:bg-panel-2">
              <span className="text-sm"><span className="num font-medium">{s.name}</span> <span className="text-xs text-muted">{s.description}</span></span>
              <Button variant={conn.symbol === s.name ? "primary" : "ghost"} onClick={() => choose(s.name)}>{conn.symbol === s.name ? "✓" : t("select")}</Button>
            </li>
          ))}
          {symbols && symbols.length === 0 && <li className="text-sm text-muted">{t("none")}</li>}
        </ul>
        {err && <div className="mt-2"><Alert tone="bad">{reasonLabel(err, lang)}</Alert></div>}
      </Card>

      {inst?.spec && (
        <Card title={t("spec")}>
          {Object.entries(inst.spec).filter(([k]) => k !== "source").map(([k, v]) => (
            <Row key={k} k={<span className="num">{k}</span>} v={v === null ? <Badge tone="bad">{t("missing")}</Badge> : <span className="num">{String(v)}</span>} />
          ))}
          <p className="mt-2 text-xs text-muted">{t("specCompare")}</p>
        </Card>
      )}
    </>
  );
}
