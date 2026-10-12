"use client";

import type { ReactNode } from "react";

export function Card({ title, children, actions, className = "" }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-panel p-4 ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-muted">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES = {
  neutral: "border-line text-muted",
  good: "border-good/40 text-good bg-good/10",
  warn: "border-warn/40 text-warn bg-warn/10",
  bad: "border-bad/40 text-bad bg-bad/10",
  accent: "border-accent/40 text-accent bg-accent/10",
} as const;

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>;
}

export function SourceBadge({ source }: { source?: string | null }) {
  if (!source) return null;
  const tone = source === "MOCK" || source === "TEST" ? "bad" : source === "LIVE" ? "warn" : "accent";
  return <Badge tone={tone}>{source}</Badge>;
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const styles = {
    primary: "bg-accent text-white hover:opacity-90",
    ghost: "border border-line text-text hover:bg-panel-2",
    danger: "border border-bad/50 text-bad hover:bg-bad/10",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`rounded-lg px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}>
      {children}
    </button>
  );
}

export function Row({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line/60 py-1.5 text-sm last:border-0">
      <span className="text-muted">{k}</span>
      <span className="text-end">{v}</span>
    </div>
  );
}

export function Alert({ tone = "warn", children }: { tone?: "warn" | "bad" | "good" | "accent"; children: ReactNode }) {
  const c = { warn: "border-warn/40 bg-warn/10", bad: "border-bad/40 bg-bad/10", good: "border-good/40 bg-good/10", accent: "border-accent/40 bg-accent/10" }[tone];
  return <div className={`rounded-lg border px-3 py-2 text-sm ${c}`}>{children}</div>;
}

export function Num({ v, d = 2 }: { v: number | null | undefined; d?: number }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className="text-muted">—</span>;
  return <span className="num">{v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}</span>;
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-muted">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}
