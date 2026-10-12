"use client";

import { useCallback, useSyncExternalStore } from "react";

/** A per-browser preference in localStorage, read via useSyncExternalStore (SSR-safe, no effect setState). */
const EVENT = "nextrade-pref";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage blocked (private mode etc.) — fall back to the default
  }
}

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(EVENT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(EVENT, cb);
  };
}

export function usePref<T extends string>(key: string, allowed: readonly T[], fallback: T): [T, (v: T) => void] {
  const raw = useSyncExternalStore(subscribe, () => read(key), () => null);
  const value = raw !== null && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
  const set = useCallback(
    (v: T) => {
      try {
        localStorage.setItem(key, v);
      } catch {
        /* ignore */
      }
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );
  return [value, set];
}
