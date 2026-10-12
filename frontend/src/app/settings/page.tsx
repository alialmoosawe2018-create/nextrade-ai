"use client";

import { useState } from "react";

import { Alert, Button, Card, Field } from "@/components/ui";
import { api, ApiError, usePoll } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";

type Values = Record<string, unknown>;
type SettingsResp = { values: Record<string, Values>; defaults: Record<string, Values>; config_hash: string };

const SECTIONS: { name: string; key: Key }[] = [
  { name: "risk", key: "sec_risk" },
  { name: "strategy", key: "sec_strategy" },
  { name: "costs", key: "sec_costs" },
  { name: "paper", key: "sec_paper" },
];

const HELP: Record<string, { ar: string; en: string }> = {
  risk_per_trade_pct: { ar: "نسبة الرصيد المخاطَر بها في الصفقة (حد أقصى 2% في الكود)", en: "% of equity risked per trade (code cap 2%)" },
  max_daily_loss_pct: { ar: "يتوقف التداول الورقي لليوم عند هذه الخسارة (حد أقصى 5%)", en: "Paper trading stops for the day at this loss (cap 5%)" },
  max_spread: { ar: "بوحدات السعر (نقاط المؤشر). فارغ = كل صفقة تُرفض", en: "In price units (index points). Empty = every trade rejected" },
  max_data_age_seconds: { ar: "أقدم عمر مسموح لآخر شمعة عند القرار", en: "Max age of the last bar at decision time" },
  trading_enabled: { ar: "مفتاح الإيقاف: أطفئه لرفض كل الصفقات", en: "Kill switch: off rejects every trade" },
  opening_range_minutes: { ar: "دقائق نطاق الافتتاح بعد 9:30 نيويورك", en: "Opening-range minutes after 09:30 NY" },
  reward_risk: { ar: "الهدف كمضاعف للمخاطرة", en: "Target as a multiple of risk" },
  slippage: { ar: "انزلاق مفترض لكل تنفيذ سوقي (نقاط)", en: "Assumed slippage per market fill (points)" },
  starting_balance: { ar: "رصيد افتراضي — ليس مالاً حقيقياً", en: "Simulated balance — not real money" },
};
const READONLY = new Set(["allowed_modes", "name"]);

export default function Settings() {
  const { t, lang } = useI18n();
  const { data, reload } = usePoll<SettingsResp>("/api/settings", 60000);
  const [edited, setEdited] = useState<Record<string, Values>>({});
  const [msgs, setMsgs] = useState<Record<string, { tone: "good" | "bad"; text: string }>>({});

  const drafts: Record<string, Values> = { ...(data?.values ?? {}), ...edited };

  async function save(name: string) {
    try {
      const r = await api<{ live_monitor_stopped: boolean }>(`/api/settings/${name}`, { method: "PUT", json: drafts[name] });
      setMsgs((m) => ({ ...m, [name]: { tone: "good", text: r.live_monitor_stopped ? t("savedStopsLive") : t("saved") } }));
      setEdited((d) => {
        const rest = { ...d };
        delete rest[name];
        return rest;
      });
      reload();
    } catch (e) {
      setMsgs((m) => ({ ...m, [name]: { tone: "bad", text: e instanceof ApiError ? e.message : String(e) } }));
    }
  }

  async function reset(name: string) {
    await api(`/api/settings/${name}/reset`, { method: "POST" });
    setEdited((d) => {
      const rest = { ...d };
      delete rest[name];
      return rest;
    });
    setMsgs((m) => ({ ...m, [name]: { tone: "good", text: t("saved") } }));
    reload();
  }

  function set(name: string, key: string, v: unknown) {
    setEdited((d) => ({ ...d, [name]: { ...(d[name] ?? data?.values[name] ?? {}), [key]: v } }));
  }

  if (!data) return <p className="text-muted">{t("loading")}</p>;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">{t("configHash")}: <span className="num">{data.config_hash}</span></p>
      {SECTIONS.map(({ name, key }) => (
        <Card key={name} title={t(key)} actions={<div className="flex gap-2"><Button variant="ghost" onClick={() => reset(name)}>{t("reset")}</Button><Button onClick={() => save(name)}>{t("save")}</Button></div>}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(drafts[name] ?? {}).map(([k, v]) => {
              const def = data.defaults[name]?.[k];
              const hint = HELP[k]?.[lang];
              if (READONLY.has(k) || Array.isArray(v)) {
                return <Field key={k} label={<span className="num">{k}</span>} hint={hint}><input className="num" value={Array.isArray(v) ? v.join(", ") : String(v)} disabled /></Field>;
              }
              if (typeof v === "boolean" || typeof def === "boolean") {
                return <Field key={k} label={<span className="num">{k}</span>} hint={hint}><input type="checkbox" checked={Boolean(v)} onChange={(e) => set(name, k, e.target.checked)} /></Field>;
              }
              if (k === "stop_method") {
                return <Field key={k} label={<span className="num">{k}</span>}><select value={String(v)} onChange={(e) => set(name, k, e.target.value)}><option value="retest_swing">retest_swing</option><option value="range_opposite">range_opposite</option></select></Field>;
              }
              const numeric = typeof v === "number" || typeof def === "number" || v === null;
              return (
                <Field key={k} label={<span className="num">{k}</span>} hint={hint}>
                  <input
                    className="num"
                    type={numeric && k !== "version" ? "number" : "text"}
                    step="any"
                    value={v === null || v === undefined ? "" : String(v)}
                    placeholder={v === null ? "—" : undefined}
                    onChange={(e) => set(name, k, numeric && k !== "version" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
                  />
                </Field>
              );
            })}
          </div>
          {msgs[name] && <div className="mt-3"><Alert tone={msgs[name].tone}>{msgs[name].text}</Alert></div>}
        </Card>
      ))}
    </div>
  );
}
