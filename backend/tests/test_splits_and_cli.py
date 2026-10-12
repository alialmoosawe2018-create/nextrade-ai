from __future__ import annotations

from datetime import date

import pytest

from app.core.clock import MarketCalendar
from app.market.providers import MockProvider
from app.services.splits import HoldoutLedger, chronological_split, data_fingerprint


@pytest.fixture(scope="module")
def sessions():
    return MarketCalendar().sessions_between(date(2026, 1, 5), date(2026, 3, 31))


def test_split_is_chronological_and_disjoint(sessions):
    sp = chronological_split(sessions)
    days = [s.day for part in ("dev", "validation", "holdout") for s in sp[part]]
    assert days == sorted(days) and len(days) == len(set(days)) == len(sessions)
    assert max(s.day for s in sp["dev"]) < min(s.day for s in sp["validation"])
    assert max(s.day for s in sp["validation"]) < min(s.day for s in sp["holdout"])
    with pytest.raises(ValueError):
        chronological_split(sessions, (0.5, 0.5, 0.5))


def test_holdout_ledger_refuses_reuse(tmp_path, sessions):
    bars = MockProvider(seed=1).bars(sessions)
    fp = data_fingerprint(bars, chronological_split(sessions)["holdout"])
    ledger = HoldoutLedger(tmp_path / "ledger.jsonl")
    ledger.check_and_record(fp, "cfgA", force=False)
    with pytest.raises(PermissionError):
        ledger.check_and_record(fp, "cfgB", force=False)
    forced = ledger.check_and_record(fp, "cfgB", force=True, note="owner approved")
    assert forced["forced_reuse"] is True and len(ledger.entries()) == 2


def test_fingerprint_changes_with_data(sessions):
    part = chronological_split(sessions)["holdout"]
    a = MockProvider(seed=1).bars(sessions)
    b = a.copy()
    b.loc[b.index[-1], "close"] += 1
    assert data_fingerprint(a, part) != data_fingerprint(b, part)


def test_backtest_cli_refuses_unverified_instrument(capsys, tmp_path):
    from app import cli
    rc = cli.main(["backtest", "--data", str(tmp_path / "x.parquet"), "--start", "2026-01-05",
                   "--end", "2026-03-31", "--split", "dev"])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out


def test_demo_cli_writes_labelled_run(tmp_path, monkeypatch, capsys):
    from app import cli
    monkeypatch.setattr(cli, "DATA", tmp_path)
    rc = cli.main(["demo", "--start", "2026-06-01", "--end", "2026-07-31"])
    out = capsys.readouterr().out
    assert rc == 0 and "MOCK DATA" in out
    run = next((tmp_path / "runs").iterdir())
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "MOCK DATA" in html and "NOT market evidence" in html
    assert (run / "trades.csv").exists() and (run / "journal.csv").exists()
