"""SQLite store for the learning loop (data/nextrade.db, local file, gitignored).

Tables: trades (with the features known at entry), reviews (Claude's hypotheses about losses),
lessons (evidence-backed rules and their status), ideas (Claude's trade ideas and their tracked outcome).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  uid TEXT PRIMARY KEY, origin TEXT NOT NULL, run_id TEXT, candidate_id TEXT, symbol TEXT, direction TEXT,
  config_hash TEXT, data_source TEXT, entry_time TEXT, exit_time TEXT, entry_price REAL, sl REAL, tp REAL,
  exit_price REAL, exit_reason TEXT, r_multiple REAL, net_pnl REAL, mfe REAL, mae REAL, ambiguous INTEGER,
  features TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_run ON trades(run_id);
CREATE TABLE IF NOT EXISTS reviews (
  uid TEXT PRIMARY KEY, verdict TEXT, factors TEXT, explanation TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, run_id TEXT, symbol TEXT, feature TEXT, op TEXT, value REAL, status TEXT,
  evidence TEXT, created_at TEXT, decided_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY, symbol TEXT, data TEXT, outcome TEXT, r_multiple REAL, resolved_at TEXT, created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Db:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _rows(self, sql: str, args=()) -> list[dict]:
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def _exec(self, sql: str, args=()) -> None:
        with self._lock, self._conn() as c:
            c.execute(sql, args)

    # ---------------------------------------------------------------- trades
    def add_trades(self, origin: str, run_id: str | None, symbol: str, trades: list, features_by_candidate: dict) -> int:
        rows = []
        for t in trades:
            uid = f"{origin}:{run_id or '-'}:{t.candidate_id}:{t.entry_time}"
            rows.append((uid, origin, run_id, t.candidate_id, symbol, t.direction, t.config_hash, t.data_source,
                         str(t.entry_time), str(t.exit_time), t.entry_price, t.stop, t.target, t.exit_price,
                         t.exit_reason, t.r_multiple, t.net_pnl, t.mfe, t.mae, int(t.ambiguous_bar),
                         json.dumps(features_by_candidate.get(t.candidate_id) or {}), _now()))
        with self._lock, self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO trades VALUES (" + ",".join("?" * 22) + ")", rows)
        return len(rows)

    def trades_df(self, run_id: str | None = None, origin: str | None = None) -> pd.DataFrame:
        sql, args = "SELECT * FROM trades WHERE 1=1", []
        if run_id:
            sql, args = sql + " AND run_id = ?", args + [run_id]
        if origin:
            sql, args = sql + " AND origin = ?", args + [origin]
        rows = self._rows(sql + " ORDER BY entry_time", args)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        feats = pd.DataFrame([json.loads(f or "{}") for f in df["features"]])
        return pd.concat([df.drop(columns=["features"]), feats], axis=1)

    def latest_backtest_run(self, symbol: str | None = None) -> str | None:
        sql, args = "SELECT run_id FROM trades WHERE origin='backtest'", []
        if symbol:
            sql, args = sql + " AND symbol = ?", [symbol]
        rows = self._rows(sql + " ORDER BY created_at DESC, run_id DESC LIMIT 1", args)
        return rows[0]["run_id"] if rows else None

    # --------------------------------------------------------------- reviews
    def add_review(self, uid: str, verdict: str, factors: list, explanation: str, model: str) -> None:
        self._exec("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?)",
                   (uid, verdict, json.dumps(factors, ensure_ascii=False), explanation, model, _now()))

    def reviews(self) -> dict[str, dict]:
        return {r["uid"]: {**r, "factors": json.loads(r["factors"] or "[]")} for r in self._rows("SELECT * FROM reviews")}

    # --------------------------------------------------------------- lessons
    def replace_open_lessons(self, run_id: str, symbol: str, lessons: list[dict]) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM lessons WHERE status IN ('VALIDATED','UNVALIDATED') AND symbol = ?", (symbol,))
            for l in lessons:
                c.execute("INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (l["id"], run_id, symbol, l["feature"], l["op"], l["value"], l["status"],
                           json.dumps(l["evidence"], ensure_ascii=False, default=str), _now(), None, ""))

    def lessons(self) -> list[dict]:
        return [{**r, "evidence": json.loads(r["evidence"] or "{}")} for r in
                self._rows("SELECT * FROM lessons ORDER BY created_at DESC")]

    def lesson(self, lid: str) -> dict | None:
        rows = [l for l in self.lessons() if l["id"] == lid]
        return rows[0] if rows else None

    def set_lesson_status(self, lid: str, status: str, note: str = "") -> None:
        self._exec("UPDATE lessons SET status=?, decided_at=?, note=? WHERE id=?", (status, _now(), note, lid))

    # ----------------------------------------------------------------- ideas
    def add_idea(self, idea: dict) -> None:
        self._exec("INSERT OR REPLACE INTO ideas VALUES (?,?,?,?,?,?,?)",
                   (idea["id"], idea["symbol"], json.dumps(idea, ensure_ascii=False, default=str), None, None, None,
                    idea["created_at"]))

    def ideas(self) -> list[dict]:
        return [{**json.loads(r["data"]), "outcome": r["outcome"], "outcome_r": r["r_multiple"],
                 "resolved_at": r["resolved_at"]} for r in self._rows("SELECT * FROM ideas ORDER BY created_at DESC")]

    def set_idea_outcome(self, iid: str, outcome: str, r: float | None) -> None:
        self._exec("UPDATE ideas SET outcome=?, r_multiple=?, resolved_at=? WHERE id=?", (outcome, r, _now(), iid))
