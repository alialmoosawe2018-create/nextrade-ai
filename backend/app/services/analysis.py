"""Research analysis on broker history pulled through the read-only gateway."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from app.brokers.mt5_gateway import Mt5Gateway, to_utc_bars
from app.brokers.server_time import choose, score_candidates
from app.core.clock import MarketCalendar
from app.core.config import CostModel, InstrumentSpec, OrbConfig, RiskConfig, config_hash
from app.core.models import DataSource
from app.market import quality
from app.reporting.metrics import summarize
from app.reporting.runs import write_run
from app.services.backtest import run_backtest
from app.services.splits import HoldoutLedger, chronological_split, data_fingerprint
from app.storage.store import Profile, ProfileStore, ServerTzRecord


class AnalysisBlocked(Exception):
    def __init__(self, reason: str, **details) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


def ensure_server_tz(gw: Mt5Gateway, profiles: ProfileStore, profile: Profile, symbol: str,
                     start: date, end: date) -> ServerTzRecord:
    """Use the profile's decided server timezone, or decide it from history now (never guess)."""
    if profile.server_tz is not None:
        return profile.server_tz
    cal = MarketCalendar()
    sessions = cal.sessions_between(start, end)
    raw = gw.rates_m1_server(symbol, sessions[0].open_utc, sessions[-1].close_utc) if sessions else pd.DataFrame()
    if raw.empty:
        raise AnalysisBlocked("NO_HISTORY", explanation=f"MT5 returned no M1 bars for {symbol} in {start}..{end}. "
                                                        "Check the symbol and that its history is downloaded in MT5.")
    scores = score_candidates(raw, sessions) if len(raw) else []
    tz, why = choose(scores)
    if tz is None:
        raise AnalysisBlocked(
            "SERVER_TIME_UNDETERMINED", explanation=why,
            scores=[{"rule": s["name"], "score": s["score"], "days": s["days_tested"]} for s in scores])
    rec = ServerTzRecord(kind=tz.kind, hours=tz.hours, zone=tz.zone, evidence=why,
                         decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    profile.server_tz = rec
    profiles.save(profile)
    return rec


def run_analysis(gw: Mt5Gateway, profiles: ProfileStore, profile: Profile, symbol: str, spec: InstrumentSpec,
                 orb: OrbConfig, risk: RiskConfig, costs: CostModel, balance: float, start: date, end: date,
                 split: str, data_dir: Path, allow_holdout_reuse: bool = False, db=None) -> dict:
    if spec.missing_fields():
        raise AnalysisBlocked("SPEC_INCOMPLETE", missing=spec.missing_fields())
    if risk.max_spread is None:
        raise AnalysisBlocked("SPREAD_LIMIT_NOT_CONFIGURED",
                              explanation="Set the maximum spread in Risk settings first (every trade would be rejected).")
    cal = MarketCalendar()
    sessions = cal.sessions_between(start, end)
    if not sessions:
        raise AnalysisBlocked("NO_SESSIONS", explanation="No US sessions in the selected range")
    tz_rec = ensure_server_tz(gw, profiles, profile, symbol, start, end)
    raw = gw.rates_m1_server(symbol, sessions[0].open_utc, sessions[-1].close_utc)
    history_dir = data_dir / "history" / (profile.server or profile.kind)
    history_dir.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(history_dir / f"{symbol}_M1_{start}_{end}.parquet", index=False)

    bars = to_utc_bars(raw, tz_rec.to_tz())
    report = quality.check(bars, sessions)
    if report.verdict in ("FAIL", "INSUFFICIENT DATA"):
        cleaned, stats = quality.clean(bars)
        report2 = quality.check(cleaned, sessions)
        if report2.verdict in ("FAIL", "INSUFFICIENT DATA"):
            raise AnalysisBlocked("DATA_QUALITY", verdict=report2.verdict, quality=asdict(report2))
        bars, report = cleaned, report2

    source = DataSource.MOCK if gw.info and gw.info.source == "MOCK" else DataSource.HISTORICAL
    parts = chronological_split(sessions) if split != "all" else {"all": sessions}
    part = parts[split]
    if not part:
        raise AnalysisBlocked("EMPTY_SPLIT", explanation=f"No sessions fall in the {split} split")
    extra = {"split": split, "server_tz": tz_rec.model_dump(), "symbol": symbol, "server": profile.server,
             "quality_verdict": report.verdict, "first_history_bar": report.first_bar}
    if split == "holdout":
        fp = data_fingerprint(bars, part)
        HoldoutLedger(data_dir / "holdout_ledger.jsonl").check_and_record(
            fp, config_hash(orb, risk, costs, spec), force=allow_holdout_reuse)
        extra["holdout_fingerprint"] = fp

    prior = [s for s in sessions if s.day < part[0].day][-40:]  # context only, never traded
    results = {}
    for k in (1.0, 1.5, 2.0):
        res = run_backtest(bars, part, spec, orb, risk, costs.scaled(k), balance, source, prior_sessions=prior)
        results[k] = (res, summarize(res.trades, balance, len(part)))
    base, summary = results[1.0]
    summary["cost_sensitivity"] = {f"x{k}": {"trades": s.get("trades"),
                                             "expectancy_r": (s.get("r") or {}).get("expectancy_r")}
                                   for k, (_, s) in results.items()}
    if source is DataSource.MOCK:
        summary = {"WARNING": "MOCK DATA — not market evidence", **summary}
    out = write_run(data_dir / "runs", f"{split}-{symbol}", base, bars, part, summary,
                    {"strategy": orb.model_dump(mode="json"), "risk": risk.model_dump(mode="json"),
                     "costs": costs.model_dump(mode="json"), "instrument": spec.model_dump(mode="json")}, extra)
    if db is not None:
        db.add_trades("backtest", out.name, symbol, base.trades, features_by_candidate(base.journal))
    return {"run_id": out.name, "report_url": f"/reports/{out.name}/report.html", "summary": summary,
            "quality": {"verdict": report.verdict, **asdict(report)}, "server_tz": tz_rec.model_dump(),
            "data_source": source.value, "sessions": len(part)}


def features_by_candidate(journal) -> dict:
    """candidate_id -> features recorded at the decision (RISK or FILTER journal entries)."""
    out = {}
    for j in journal:
        if j.get("stage") in ("RISK", "FILTER") and j.get("candidate_id") and j.get("features") is not None:
            out[j["candidate_id"]] = j["features"]
    return out
