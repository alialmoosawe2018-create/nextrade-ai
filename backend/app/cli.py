"""NexTrade AI command line (phase A).

  python -m app.cli demo                                   # MOCK pipeline demo, clearly labelled
  python -m app.cli quality  --data FILE --start D --end D # data-quality report
  python -m app.cli backtest --data FILE --start D --end D --split dev|validation|holdout

Real-data commands refuse to run until config/instrument.yaml is verified from MT5.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.clock import MarketCalendar
from app.core.config import (CostModel, InstrumentSpec, OrbConfig, RiskConfig, SpecSource,
                             load_yaml)
from app.core.models import DataSource
from app.market import quality
from app.market.providers import MockProvider, ParquetProvider
from app.reporting.html import render
from app.reporting.metrics import summarize
from app.services.backtest import BacktestResult, run_backtest
from app.services.splits import SPLITS, HoldoutLedger, chronological_split, data_fingerprint

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
DATA = ROOT / "data"

# TEST contract used ONLY by the MOCK demo (1 lot = 1.0 currency per 1.0 price move).
DEMO_SPEC = InstrumentSpec(symbol="MOCK100", source=SpecSource.TEST, point=0.01, digits=2,
                           tick_size=0.01, tick_value=0.01, contract_size=1.0, volume_min=0.1,
                           volume_max=50.0, volume_step=0.1, currency_profit="USD", account_currency="USD")


def _configs() -> tuple[OrbConfig, RiskConfig, CostModel, InstrumentSpec]:
    return (OrbConfig(**load_yaml(CONFIG / "strategy_orb.yaml")),
            RiskConfig(**load_yaml(CONFIG / "risk.yaml")),
            CostModel(**load_yaml(CONFIG / "costs.yaml")),
            InstrumentSpec(**load_yaml(CONFIG / "instrument.yaml")))


def _code_version() -> dict:
    """Git commit + whether the working tree had uncommitted changes when the run was made."""
    def git(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def _write_run(tag: str, result: BacktestResult, bars: pd.DataFrame, sessions, summary: dict,
               configs: dict, extra: dict | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = DATA / "runs" / f"{stamp}-{tag}-{result.config_hash[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    meta = {"tag": tag, "created_utc": stamp, "config_hash": result.config_hash,
            "data_source": result.data_source.value, "simulated": True, "code": _code_version(),
            "sessions": [str(sessions[0].day), str(sessions[-1].day)] if sessions else [],
            "configs": configs, **(extra or {})}
    (out / "run.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    pd.DataFrame([asdict(t) for t in result.trades]).to_csv(out / "trades.csv", index=False)
    pd.DataFrame(result.journal).to_csv(out / "journal.csv", index=False)
    title = f"NexTrade AI — {tag} ({result.data_source.value})"
    (out / "report.html").write_text(render(result, bars, sessions, summary, title), encoding="utf-8")
    return out


def cmd_demo(args) -> int:
    orb, risk, costs, _ = _configs()
    # The demo needs a spread limit to exercise the pipeline; real runs must set their own.
    risk = risk.model_copy(update={"max_spread": risk.max_spread or 5.0})
    cal = MarketCalendar()
    sessions = cal.sessions_between(date.fromisoformat(args.start), date.fromisoformat(args.end))
    bars = MockProvider(seed=args.seed).bars(sessions)
    result = run_backtest(bars, sessions, DEMO_SPEC, orb, risk, costs, args.balance, DataSource.MOCK)
    summary = {"WARNING": "MOCK DATA — not market evidence", **summarize(result.trades, args.balance, len(sessions))}
    out = _write_run("mock-demo", result, bars, sessions, summary,
                     {"strategy": orb.model_dump(mode="json"), "risk": risk.model_dump(mode="json"),
                      "costs": costs.model_dump(mode="json"), "instrument": DEMO_SPEC.model_dump(mode="json")})
    print("MOCK DATA — this run only proves the pipeline works; its numbers mean nothing.")
    print(f"sessions={len(sessions)} trades={len(result.trades)} status={summary['status']}")
    print(f"report: {out / 'report.html'}")
    return 0


def _load_real(args):
    orb, risk, costs, spec = _configs()
    if spec.source is not SpecSource.MT5_SYMBOL_INFO or spec.missing_fields():
        print("REFUSED: config/instrument.yaml is not verified from MT5 "
              f"(source={spec.source.value}, missing={spec.missing_fields()}). Phase A2 fills it.")
        return None
    cal = MarketCalendar()
    sessions = cal.sessions_between(date.fromisoformat(args.start), date.fromisoformat(args.end))
    bars, stats = quality.clean(ParquetProvider(Path(args.data)).bars())
    return orb, risk, costs, spec, sessions, bars, stats


def cmd_quality(args) -> int:
    cal = MarketCalendar()
    sessions = cal.sessions_between(date.fromisoformat(args.start), date.fromisoformat(args.end))
    raw = ParquetProvider(Path(args.data)).bars()
    report = quality.check(raw, sessions)
    print(json.dumps({"verdict": report.verdict, **asdict(report)}, indent=2, default=str))
    return 0 if report.verdict in ("OK", "WARN") else 1


def cmd_backtest(args) -> int:
    loaded = _load_real(args)
    if loaded is None:
        return 2
    orb, risk, costs, spec, sessions, bars, stats = loaded
    part = chronological_split(sessions)[args.split]
    extra = {"split": args.split, "cleaning": stats}
    if args.split == "holdout":
        from app.core.config import config_hash
        fp = data_fingerprint(bars, part)
        HoldoutLedger(DATA / "holdout_ledger.jsonl").check_and_record(
            fp, config_hash(orb, risk, costs, spec), force=args.allow_holdout_reuse, note=args.note or "")
        extra["holdout_fingerprint"] = fp
    results = {}
    for k in (1.0, 1.5, 2.0):
        res = run_backtest(bars, part, spec, orb, risk, costs.scaled(k), args.balance, DataSource.HISTORICAL)
        results[k] = (res, summarize(res.trades, args.balance, len(part)))
    base, base_summary = results[1.0]
    base_summary["cost_sensitivity"] = {f"x{k}": {"trades": s.get("trades"),
                                                  "expectancy_r": (s.get("r") or {}).get("expectancy_r")}
                                        for k, (_, s) in results.items()}
    out = _write_run(f"backtest-{args.split}", base, bars, part, base_summary,
                     {"strategy": orb.model_dump(mode="json"), "risk": risk.model_dump(mode="json"),
                      "costs": costs.model_dump(mode="json"), "instrument": spec.model_dump(mode="json")}, extra)
    print(f"SIMULATED backtest on {args.split}: trades={base_summary['trades']} status={base_summary['status']}")
    print(f"report: {out / 'report.html'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nextrade")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="MOCK end-to-end pipeline demo")
    d.add_argument("--start", default="2026-04-01")
    d.add_argument("--end", default="2026-09-30")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--balance", type=float, default=10_000.0)
    d.set_defaults(fn=cmd_demo)
    for name, fn in (("quality", cmd_quality), ("backtest", cmd_backtest)):
        q = sub.add_parser(name)
        q.add_argument("--data", required=True)
        q.add_argument("--start", required=True)
        q.add_argument("--end", required=True)
        if name == "backtest":
            q.add_argument("--split", choices=SPLITS, required=True)
            q.add_argument("--balance", type=float, default=10_000.0)
            q.add_argument("--allow-holdout-reuse", action="store_true")
            q.add_argument("--note")
        q.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
