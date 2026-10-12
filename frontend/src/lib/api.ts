"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

type Detail = { code?: string; message?: string; explanation?: string } | string | { field: string; error: string }[];

function describe(detail: Detail | undefined, fallback: string): { msg: string; code?: string } {
  if (!detail) return { msg: fallback };
  if (typeof detail === "string") return { msg: detail };
  if (Array.isArray(detail)) return { msg: detail.map((d) => `${d.field}: ${d.error}`).join(" · ") };
  return { msg: detail.message || detail.explanation || detail.code || fallback, code: detail.code };
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = (body as { detail?: Detail } | null)?.detail;
    const { msg, code } = describe(detail, res.status === 500 || res.status === 502 || res.status === 504 ? "backend unreachable" : res.statusText);
    throw new ApiError(res.status, msg, code, detail);
  }
  return body as T;
}

/** Fetch on mount and every `ms` while the page is visible. Cleans up when the page is hidden. */
export function usePoll<T>(path: string | null, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    if (!path) return;
    const first = setTimeout(load, 0);
    const id = setInterval(load, ms);
    return () => {
      alive.current = false;
      clearTimeout(first);
      clearInterval(id);
    };
  }, [path, ms, load]);

  return { data, error, reload: load };
}

// ------------------------------------------------------------------ types
export type ServerTzInfo = { kind: string; hours: number; zone: string | null; evidence: string; decided_at: string };

export type Connection =
  | { connected: false }
  | {
      connected: true;
      profile_id: string | null;
      profile_label: string | null;
      method: string;
      source: "DEMO" | "LIVE" | "MOCK";
      login: number | null;
      server: string | null;
      company: string | null;
      currency: string | null;
      account_mode: string;
      read_only: boolean;
      terminal_build: number | null;
      connected_at: string;
      balance: number | null;
      equity: number | null;
      warnings: string[];
      symbol: string | null;
      server_tz: ServerTzInfo | null;
    };

export type Health = {
  status: string;
  version: string;
  mode: string;
  real_trading: string;
  mt5_package: string;
  connection: Connection;
  live_monitor: string;
  recent_errors: { at: string; where: string; error: string }[];
};

export type Profile = {
  id: string;
  label: string;
  kind: "MT5" | "MOCK";
  server: string;
  login: number | null;
  terminal_path: string | null;
  symbol: string | null;
  server_tz: ServerTzInfo | null;
  created_at: string;
};

export type SymbolRow = { name: string; description: string; path: string; nasdaq_candidate: boolean };

export type Spec = Record<string, string | number | null>;

export type RunRow = {
  run_id: string;
  report_url: string;
  data_source: string;
  split: string | null;
  symbol: string | null;
  created_utc: string;
  status: string;
  trades: number | null;
  expectancy_r: number | null;
};

export type JournalEvent = {
  session_day: string;
  time: string | null;
  stage: string;
  status: string;
  reasons: string[];
  [k: string]: unknown;
};

export type LiveSnapshot = {
  status: string;
  message?: string;
  error?: string | null;
  simulated?: boolean;
  data_source?: string;
  symbol?: string;
  server_tz?: string;
  now_utc?: string;
  session?: { day: string; open_utc: string; close_utc: string; early_close: boolean } | null;
  phase?: string | null;
  or_high?: number | null;
  or_low?: number | null;
  direction?: string | null;
  last_bar_time?: string | null;
  data_age_seconds?: number | null;
  paper_balance?: number;
  open_positions?: { direction: string; entry_price: number; stop: number; target: number; volume: number; entry_time: string; exit_deadline: string }[];
  events?: JournalEvent[];
  trades?: { candidate_id: string; direction: string; entry_time: string; exit_time: string; exit_reason: string; net_pnl: number; r_multiple: number | null }[];
};
