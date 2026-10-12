"""Writes a research run folder: run.json, summary.json, trades.csv, journal.csv, report.html."""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.reporting.html import render
from app.services.backtest import BacktestResult

ROOT = Path(__file__).resolve().parents[3]


def code_version() -> dict:
    """Git commit + whether the working tree had uncommitted changes when the run was made."""
    def git(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def write_run(runs_dir: Path, tag: str, result: BacktestResult, bars: pd.DataFrame, sessions, summary: dict,
              configs: dict, extra: dict | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(runs_dir) / f"{stamp}-{tag}-{result.config_hash[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    meta = {"tag": tag, "created_utc": stamp, "config_hash": result.config_hash,
            "data_source": result.data_source.value, "simulated": True, "code": code_version(),
            "sessions": [str(sessions[0].day), str(sessions[-1].day)] if sessions else [],
            "configs": configs, **(extra or {})}
    (out / "run.json").write_text(json.dumps(meta, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame([asdict(t) for t in result.trades]).to_csv(out / "trades.csv", index=False)
    pd.DataFrame(result.journal).to_csv(out / "journal.csv", index=False)
    title = f"NexTrade AI — {tag} ({result.data_source.value})"
    (out / "report.html").write_text(render(result, bars, sessions, summary, title), encoding="utf-8")
    return out
