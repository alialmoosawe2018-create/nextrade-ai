"""Self-contained HTML report for VISUAL review of trades and no-signal days.

The numbers alone are not enough: every run should be eyeballed on a few
days and compared with the same days in MT5.
"""
from __future__ import annotations

import html
import json
import random
from collections import Counter

import pandas as pd

from app.core.clock import NEW_YORK
from app.core.models import Session
from app.services.backtest import BacktestResult

LWC = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"


def _day_payload(bars: pd.DataFrame, s: Session, result: BacktestResult) -> dict:
    t = pd.to_datetime(bars["time"], utc=True)
    day = bars[(t >= s.open_utc) & (t < s.close_utc)]
    candles = [{"time": int(pd.Timestamp(r.time).timestamp()), "open": r.open, "high": r.high,
                "low": r.low, "close": r.close} for r in day.itertuples()]
    entries = [j for j in result.journal if j["session_day"] == s.day]
    risk = next((j for j in entries if j["stage"] == "RISK"), None)
    reason = next((j for j in entries if j["stage"] == "STRATEGY"), None)
    trade = next((tr for tr in result.trades if tr.session_day == s.day), None)
    lines, markers = [], []
    or_info = (reason or risk or {})
    for key, color in (("or_high", "#8aa4ff"), ("or_low", "#8aa4ff")):
        if or_info.get(key) is not None:
            lines.append({"price": or_info[key], "color": color, "title": key.upper()})
    if risk:
        lines += [{"price": risk["stop"], "color": "#ff6b6b", "title": "STOP"},
                  {"price": risk["target"], "color": "#4cd4a0", "title": "TARGET"}]
    if trade:
        long = trade.direction == "LONG"
        markers.append({"time": int(trade.entry_time.timestamp()), "position": "belowBar" if long else "aboveBar",
                        "color": "#ffd166", "shape": "arrowUp" if long else "arrowDown",
                        "text": f"ENTRY {trade.entry_price:.2f}"})
        markers.append({"time": int(trade.exit_time.timestamp()), "position": "aboveBar" if long else "belowBar",
                        "color": "#ffffff", "shape": "circle",
                        "text": f"{trade.exit_reason} {trade.r_multiple:+.2f}R"})
    status = (f"{trade.direction} trade: {trade.exit_reason}, {trade.r_multiple:+.2f}R, net {trade.net_pnl:+.2f}"
              if trade else (f"no trade: {', '.join(risk['reasons'])}" if risk and risk["status"] == "REJECTED"
                             else f"no signal: {reason['reasons'][0]}" if reason else "no data"))
    return {"day": str(s.day), "open_ny": s.open_utc.tz_convert(NEW_YORK).strftime("%H:%M"),
            "early_close": s.early_close, "candles": candles, "lines": lines,
            "markers": sorted(markers, key=lambda m: m["time"]), "status": status}


def render(result: BacktestResult, bars: pd.DataFrame, sessions: list[Session], summary: dict,
           title: str, sample_days: int = 5, seed: int = 0) -> str:
    rng = random.Random(seed)
    trade_days = {t.session_day for t in result.trades}
    with_trade = [s for s in sessions if s.day in trade_days]
    without = [s for s in sessions if s.day not in trade_days]
    pick = rng.sample(with_trade, min(len(with_trade), max(sample_days - 2, 1))) if with_trade else []
    pick += rng.sample(without, min(len(without), sample_days - len(pick)))
    days = [_day_payload(bars, s, result) for s in sorted(pick, key=lambda s: s.day)]

    reasons = Counter(r for j in result.journal for r in j["reasons"])
    is_mock = result.data_source.value in ("MOCK", "TEST")
    banner = ("<div class='banner'>MOCK DATA — synthetic random walk. These numbers are NOT market evidence "
              "and say nothing about the strategy.</div>" if is_mock else
              f"<div class='banner sim'>SIMULATED results on {html.escape(result.data_source.value)} data. "
              "Not live trading. Past simulated results do not guarantee future results.</div>")
    summary_rows = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(json.dumps(v, default=str, ensure_ascii=False))}</td></tr>"
        for k, v in summary.items())
    reason_rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in reasons.most_common())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<script src="{LWC}"></script>
<style>
:root{{--bg:#0f1218;--panel:#171b23;--text:#e6e9ef;--muted:#9aa3b2;--line:#2a3140;--warn:#ff6b6b;--sim:#ffd166}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:16px}}
.banner{{background:var(--warn);color:#111;font-weight:700;padding:10px 14px;border-radius:6px;margin-bottom:16px}}
.banner.sim{{background:var(--sim)}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:24px 0 8px}}
.muted{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;background:var(--panel);font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top;word-break:break-word}}
th{{color:var(--muted);font-weight:500;width:32%}}
.day{{background:var(--panel);border-radius:6px;padding:12px;margin:12px 0}}
.chart{{height:340px}}
</style></head><body><main>
{banner}
<h1>{html.escape(title)}</h1>
<div class="muted">Config hash {html.escape(result.config_hash)} · data {html.escape(result.data_source.value)} ·
{len(sessions)} sessions · chart times are UTC</div>
<h2>Summary</h2><table>{summary_rows}</table>
<h2>Why days produced no trade (journal reasons)</h2><table>{reason_rows}</table>
<h2>Sample days for visual review</h2>
<div class="muted">Compare each of these with the same day in MT5 before trusting any number above.</div>
<div id="days"></div>
</main>
<script>
const DAYS = {json.dumps(days)};
const root = document.getElementById('days');
for (const d of DAYS) {{
  const box = document.createElement('div'); box.className = 'day';
  box.innerHTML = `<b>${{d.day}}</b> <span class="muted">(NY open ${{d.open_ny}}${{d.early_close ? ', early close' : ''}})</span> — ${{d.status}}<div class="chart"></div>`;
  root.appendChild(box);
  if (!window.LightweightCharts || !d.candles.length) continue;
  const chart = LightweightCharts.createChart(box.querySelector('.chart'), {{
    layout: {{ background: {{ color: '#171b23' }}, textColor: '#cfd5e1' }},
    grid: {{ vertLines: {{ color: '#222836' }}, horzLines: {{ color: '#222836' }} }},
    timeScale: {{ timeVisible: true, secondsVisible: false }}, autoSize: true }});
  const s = chart.addCandlestickSeries();
  s.setData(d.candles);
  for (const l of d.lines) s.createPriceLine({{ price: l.price, color: l.color, lineWidth: 1, lineStyle: 2, title: l.title }});
  s.setMarkers(d.markers);
  chart.timeScale().fitContent();
}}
</script></body></html>"""
