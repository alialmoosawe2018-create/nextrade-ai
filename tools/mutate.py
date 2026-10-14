"""Mutation check: break one piece of code at a time and prove some test goes red.

Each mutation runs in a throwaway copy of backend/ under .mutation/ (gitignored),
so the real source is never touched. Step 0 is an identity check: the copy must
import its OWN app package (otherwise every mutation would silently test the
original code and "pass").

Usage:  .venv\\Scripts\\python.exe tools\\mutate.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORK = ROOT / ".mutation"
PY = sys.executable

# (id, file relative to backend/, [(old, new), ...]) — each `old` must occur exactly once.
MUTATIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    # --- strategy
    ("orb-bucket-first-close", "app/strategy/orb.py", [("close = self._bucket[-1].close", "close = self._bucket[0].close")]),
    ("orb-bucket-not-reset", "app/strategy/orb.py", [("            self._bucket = []\n            self._bucket_start = start", "            self._bucket_start = start")]),
    ("orb-ignore-deadline", "app/strategy/orb.py", [("if close_time <= self.breakout_deadline:", "if True:")]),
    ("orb-no-false-breakout", "app/strategy/orb.py", [("if bar.close < level - cfg.retest_max_penetration:", "if False:")]),
    ("orb-touch-sign", "app/strategy/orb.py", [("bar.low <= level + cfg.retest_tolerance", "bar.low <= level - cfg.retest_tolerance")]),
    ("orb-long-entry-no-spread", "app/strategy/orb.py", [("entry_ref = bar.close + sp", "entry_ref = bar.close")]),
    ("orb-short-stop-no-spread", "app/strategy/orb.py", [("stop = base + sp + cfg.stop_buffer", "stop = base + cfg.stop_buffer")]),
    ("orb-no-gap-check", "app/strategy/orb.py", [("if missing > self.cfg.max_gap_minutes:", "if False:")]),
    ("orb-no-range-completeness", "app/strategy/orb.py", [("if len(self._or_bars) != n:", "if False:")]),
    ("orb-premarket-accepted", "app/strategy/orb.py", [("if bar.time < self.session.open_utc or bar.time >= self.session.close_utc:", "if bar.time >= self.session.close_utc:")]),
    ("orb-second-signal", "app/strategy/orb.py", [("        self.phase = Phase.DONE\n        return [StrategyEvent(\"SIGNAL\"", "        self.phase = Phase.WAIT_BREAKOUT\n        return [StrategyEvent(\"SIGNAL\"")]),
    ("orb-no-entry-window", "app/strategy/orb.py", [("if signal_time > self.last_entry_at:", "if False:")]),
    ("orb-no-stop-distance-check", "app/strategy/orb.py", [("if not (cfg.min_stop_distance <= dist <= cfg.max_stop_distance):", "if False:")]),
    # --- execution
    ("paper-long-fill-no-spread", "app/execution/paper.py", [("fill = bar.open + sp + costs.slippage", "fill = bar.open + costs.slippage")]),
    ("paper-both-touched-target", "app/execution/paper.py", [("        if hit_stop:\n            return pos.stop - slip, \"STOP\", hit_target\n        if hit_target:\n            return pos.target, \"TARGET\", False",
                                                              "        if hit_target:\n            return pos.target, \"TARGET\", False\n        if hit_stop:\n            return pos.stop - slip, \"STOP\", hit_target")]),
    ("paper-short-exits-on-bid", "app/execution/paper.py", [("shift = 0.0 if long else sp", "shift = 0.0")]),
    ("paper-gap-fills-at-stop", "app/execution/paper.py", [("            return o - slip, \"STOP\", False", "            return pos.stop - slip, \"STOP\", False")]),
    ("paper-gap-target-bonus", "app/execution/paper.py", [("        if o >= pos.target:\n            seen(o)\n            return pos.target", "        if o >= pos.target:\n            seen(o)\n            return o")]),
    ("paper-no-time-exit", "app/execution/paper.py", [("if bar.time >= pos.exit_deadline:", "if False:")]),
    ("paper-kill-switch-ignored", "app/execution/paper.py", [("if self.kill_switch:", "if False:")]),
    ("paper-entry-bar-unchecked", "app/execution/paper.py", [("if bar.time != candidate.signal_time:", "if False:")]),
    ("paper-no-commission", "app/execution/paper.py", [("commission = 2 * self.costs.commission_per_lot_per_side * pos.volume", "commission = 0.0")]),
    ("paper-no-fill-validity", "app/execution/paper.py", [("    if through:", "    if False:")]),
    ("paper-stop-no-slippage", "app/execution/paper.py", [("            return pos.stop - slip, \"STOP\", hit_target", "            return pos.stop, \"STOP\", hit_target")]),
    ("paper-risk-decision-ignored", "app/execution/paper.py", [("if not decision.approved or decision.volume <= 0:", "if False:")]),
    # --- risk
    ("risk-round-up", "app/risk/engine.py", [("from decimal import ROUND_FLOOR, Decimal", "from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal"),
                                             ("rounding=ROUND_FLOOR", "rounding=ROUND_CEILING")]),
    ("risk-no-min-lot", "app/risk/engine.py", [("if volume < spec.volume_min:", "if False:")]),
    ("risk-daily-loss-strict", "app/risk/engine.py", [("if account.realized_pnl_today <= -daily_limit:", "if account.realized_pnl_today < -daily_limit:")]),
    ("risk-trade-limit-off-by-one", "app/risk/engine.py", [("if account.trades_today >= cfg.max_trades_per_day:", "if account.trades_today > cfg.max_trades_per_day:")]),
    ("risk-future-data-ok", "app/risk/engine.py", [("if age > cfg.max_data_age_seconds or age < 0:", "if age > cfg.max_data_age_seconds:")]),
    ("risk-real-unlocked", "app/risk/engine.py", [("if account.mode == AccountMode.REAL and not REAL_TRADING_ENABLED:", "if False:")]),
    ("risk-spec-missing-ignored", "app/risk/engine.py", [("    if missing:\n        reasons.append(\"SPEC_MISSING\")", "    if False:\n        reasons.append(\"SPEC_MISSING\")")]),
    ("risk-no-slippage-in-size", "app/risk/engine.py", [("(c.risk_distance + 2 * costs.slippage) * per_unit", "(c.risk_distance + costs.slippage) * per_unit")]),
    ("risk-no-daily-budget", "app/risk/engine.py", [("if loss_at_stop > remaining:", "if False:")]),
    ("risk-spread-loose", "app/risk/engine.py", [("elif market.spread > cfg.max_spread:", "elif market.spread > cfg.max_spread * 2:")]),
    ("risk-no-geometry", "app/risk/engine.py", [("geometry_ok = c.stop < c.entry_ref < c.target", "geometry_ok = True")]),
    ("risk-no-kill-switch", "app/risk/engine.py", [("if not cfg.trading_enabled:", "if False:")]),
    # --- safety / clock / runner / reporting / quality
    ("safety-real-enabled", "app/core/safety.py", [("REAL_TRADING_ENABLED: Final[bool] = False", "REAL_TRADING_ENABLED: Final[bool] = True")]),
    ("clock-no-early-close", "app/core/clock.py", [("early_close=minutes < REGULAR_SESSION_MINUTES", "early_close=False")]),
    ("runner-ignores-risk", "app/services/backtest.py", [("            if decision.approved:\n", "            if True:\n")]),
    ("runner-always-session-exit", "app/services/backtest.py", [("((time_exit, \"TIME_EXIT\") if time_exit <= self.session_exit\n                             else (self.session_exit, \"SESSION_END\"))", "(self.session_exit, \"SESSION_END\")")]),
    ("runner-live-clock-ignored", "app/services/backtest.py", [("market = MarketSnapshot(now=self._now(bar),", "market = MarketSnapshot(now=bar.close_time,")]),
    # runner-no-trade-count is intentionally absent: with at most one signal per day the runner can
    # never reach a second trade, so no test can observe that counter. TRADE_LIMIT itself is tested
    # in the risk engine (test_trade_and_position_limits).
    ("gateway-allows-orders", "app/brokers/mt5_gateway.py", [("\"symbols_get\", \"symbol_info\", \"symbol_select\", \"symbol_info_tick\", \"copy_rates_range\",", "\"symbols_get\", \"symbol_info\", \"symbol_select\", \"symbol_info_tick\", \"copy_rates_range\", \"order_send\",")]),
    ("gateway-real-not-flagged", "app/brokers/mt5_gateway.py", [("        if mode == \"REAL\":\n", "        if False:\n")]),
    ("api-profile-accepts-extra", "app/api/main.py", [("    model_config = ConfigDict(extra=\"forbid\")  # a \"password\" key is rejected, never stored", "    model_config = ConfigDict(extra=\"ignore\")")]),
    ("tz-choose-ignores-decisive-days", "app/brokers/server_time.py", [("        if len(decisive) < min_decisive_days or b <= r or b < 2 * len(decisive) / 3:", "        if False:")]),
    ("live-backfill-trades-stale", "app/services/live.py", [("now_fn=lambda _b: pd.Timestamp(self.clock())", "now_fn=None")]),
    ("chat-stop-goes-to-model", "app/services/chat.py", [("        if STOP_PATTERN.match(message):", "        if False:")]),
    ("chat-actions-unvalidated", "app/services/chat.py", [("            actions, dropped = validate_actions(out[\"actions\"])", "            actions, dropped = out[\"actions\"], []")]),
    ("chat-model-gets-tools", "app/services/chat.py", [("\"--tools\", \"\",\n", "\"--tools\", \"default\",\n")]),
    ("chat-risk-cap-ignored", "app/services/chat.py", [("            if a.get(\"risk_pct\") is not None and not (0 < float(a[\"risk_pct\"]) <= 2):", "            if False:")]),
    ("ideas-kill-ignored", "app/services/ideas.py", [("    if not risk.trading_enabled:", "    if False:")]),
    ("ideas-no-stop-side", "app/services/ideas.py", [("    if (buy and not sl < entry) or (not buy and not sl > entry):", "    if False:")]),
    ("ideas-no-spread-ratio", "app/services/ideas.py", [("    if q[\"spread\"] > MAX_SPREAD_TO_STOP * dist:", "    if False:")]),
    ("ideas-no-min-lot", "app/services/ideas.py", [("    if volume < spec.volume_min:", "    if False:")]),
    ("ideas-risk-cap-ignored", "app/services/ideas.py", [("    pct = min(risk_pct or risk.risk_per_trade_pct, risk.risk_per_trade_pct)", "    pct = risk_pct or risk.risk_per_trade_pct")]),
    ("assets-arabic-edge-off", "app/brokers/assets.py", [("_EDGE_L = r\"(?<![\" + _LETTER + r\"])", "_EDGE_L = r\"(?:\" + r\"\" + r\")")]),
    ("assistant-live-ignores-kill", "app/api/main.py", [("        if not st.settings.get(\"risk\").trading_enabled:\n            raise HTTPException(409, detail={\"code\": \"TRADING_DISABLED\",", "        if False:\n            raise HTTPException(409, detail={\"code\": \"TRADING_DISABLED\",")]),
    ("learning-no-validation", "app/research/learning.py", [("        validated = (s[\"n\"] >= MIN_EXCLUDED_VALIDATION", "        validated = True or (s[\"n\"] >= MIN_EXCLUDED_VALIDATION")]),
    ("learning-no-t-threshold", "app/research/learning.py", [("or d[\"t\"] is None or d[\"t\"] < T_MIN):", "or d[\"t\"] is None):")]),
    ("learning-no-min-trades", "app/research/learning.py", [("    if len(df) < MIN_TRADES:", "    if False:")]),
    ("features-lookahead", "app/research/features.py", [("    prior = prior_daily[prior_daily[\"day\"] < session.day].sort_values(\"day\")", "    prior = prior_daily.sort_values(\"day\")")]),
    ("engine-filter-ignored", "app/services/backtest.py", [("            hits = [f for f in self.orb_cfg.entry_filters if f.matches(feats)]", "            hits = []")]),
    ("approve-unvalidated", "app/api/learning.py", [("        if lesson[\"status\"] != \"VALIDATED\":", "        if False:")]),
    ("review-invented-factors", "app/api/learning.py", [("                        factors = [f for f in r.get(\"factors\", []) if f in FEATURE_DOCS]", "                        factors = list(r.get(\"factors\", []))")]),
    ("idea-same-bar-target-first", "app/research/learning.py", [("        if hit_sl:\n            return \"STOP\", -1.0\n        if hit_tp:", "        if hit_tp and hit_sl:\n            return \"TARGET\", round(abs(tp - entry) / risk, 3)\n        if hit_sl:\n            return \"STOP\", -1.0\n        if hit_tp:")]),
    ("filter-matches-none", "app/core/config.py", [("        if v is None:\n            return False", "        if v is None:\n            return True")]),
    ("metrics-fake-zero", "app/reporting/metrics.py", [("return {\"status\": \"INSUFFICIENT DATA\"", "return {\"net_pnl\": 0.0, \"status\": \"INSUFFICIENT DATA\"")]),
    ("quality-no-dup-check", "app/market/quality.py", [("r.duplicates = int(t.duplicated().sum())", "r.duplicates = 0")]),
    ("quality-clean-keeps-bad", "app/market/quality.py", [("out = out[~bad].reset_index(drop=True)", "out = out.reset_index(drop=True)")]),
]


def run_tests(copy: Path) -> tuple[bool, str]:
    p = subprocess.run([PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"], cwd=copy,
                       capture_output=True, text=True, timeout=600)
    tail = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode == 0, tail[-1] if tail else ""


def fresh_copy(name: str) -> Path:
    dst = WORK / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(BACKEND, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def main() -> int:
    WORK.mkdir(exist_ok=True)
    # Step 0a: the copy must import its own package.
    ident = fresh_copy("_identity")
    probe = subprocess.run([PY, "-c", "import app, pathlib; print(pathlib.Path(app.__file__).resolve())"],
                           cwd=ident, capture_output=True, text=True)
    imported = Path(probe.stdout.strip())
    if ident.resolve() not in imported.parents:
        print(f"IDENTITY FAIL: copy imports {imported}, not {ident}")
        return 2
    # Step 0b: an obviously-breaking mutation must be caught.
    target = ident / "app/risk/engine.py"
    target.write_text(target.read_text(encoding="utf-8").replace("    reasons: list[str] = []", "    raise RuntimeError('identity mutation')", 1), encoding="utf-8")
    ok, _ = run_tests(ident)
    if ok:
        print("IDENTITY FAIL: a raising engine still passed the suite")
        return 2
    print("identity check OK (copy imports its own code; a broken engine is caught)\n")

    survivors = []
    for mid, rel, edits in MUTATIONS:
        copy = fresh_copy(mid)
        f = copy / rel
        src = f.read_text(encoding="utf-8")
        for old, new in edits:
            n = src.count(old)
            if n != 1:
                print(f"[SETUP ERROR] {mid}: pattern found {n} times in {rel}: {old[:60]!r}")
                return 3
            src = src.replace(old, new)
        f.write_text(src, encoding="utf-8", newline="\n")
        ok, last = run_tests(copy)
        status = "SURVIVED" if ok else "killed"
        if ok:
            survivors.append(mid)
        print(f"{status:9} {mid:32} {last}")
        shutil.rmtree(copy, ignore_errors=True)
    shutil.rmtree(ident, ignore_errors=True)
    print(f"\n{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")
    if survivors:
        print("SURVIVORS:", ", ".join(survivors))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
