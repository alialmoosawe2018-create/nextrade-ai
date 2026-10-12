"""Chronological dev / validation / holdout splits and the holdout ledger.

The holdout is the final, untouched test. Every holdout run is appended to
data/holdout_ledger.jsonl. A second run on the same holdout data is refused
unless explicitly forced — and the force is recorded too.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.models import Session

SPLITS = ("dev", "validation", "holdout")


def chronological_split(sessions: list[Session], fractions=(0.6, 0.2, 0.2)) -> dict[str, list[Session]]:
    if len(fractions) != 3 or abs(sum(fractions) - 1.0) > 1e-9 or min(fractions) <= 0:
        raise ValueError("fractions must be three positive numbers summing to 1")
    ordered = sorted(sessions, key=lambda s: s.day)
    n = len(ordered)
    a = int(n * fractions[0])
    b = a + int(n * fractions[1])
    return {"dev": ordered[:a], "validation": ordered[a:b], "holdout": ordered[b:]}


def data_fingerprint(bars: pd.DataFrame, sessions: list[Session]) -> str:
    if not sessions:
        return "empty"
    lo, hi = sessions[0].open_utc, sessions[-1].close_utc
    t = pd.to_datetime(bars["time"], utc=True)
    part = bars[(t >= lo) & (t < hi)]
    h = hashlib.sha256()
    h.update(f"{lo}|{hi}|{len(part)}".encode())
    h.update(pd.util.hash_pandas_object(part.reset_index(drop=True), index=False).values.tobytes())
    return h.hexdigest()[:16]


class HoldoutLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def check_and_record(self, fingerprint: str, config_hash: str, force: bool, note: str = "") -> dict:
        prior = [e for e in self.entries() if e["fingerprint"] == fingerprint]
        if prior and not force:
            raise PermissionError(
                f"Holdout data {fingerprint} was already used {len(prior)} time(s) "
                f"(first: {prior[0]['at']}, config {prior[0]['config_hash']}). Refusing to reuse it."
            )
        entry = {"at": datetime.now(timezone.utc).isoformat(), "fingerprint": fingerprint,
                 "config_hash": config_hash, "forced_reuse": bool(prior), "note": note}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry) + "\n")
        return entry
