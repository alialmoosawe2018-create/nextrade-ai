# Trading rules — Opening Range Breakout + Retest (v1)

Status: **hypothesis under test, not a proven strategy.** Nothing here claims profitability.
Code: `backend/app/strategy/orb.py` (signals), `backend/app/execution/paper.py` (fills/exits),
`backend/app/risk/engine.py` (risk). Reason codes below appear verbatim in the journal.

## 1. Data and time
- Input: completed **M1 bars**, bar time = bar **open** time in **UTC**, aligned to the minute.
- Prices are **bid** OHLC (MT5 builds CFD candles on the bid — to be re-verified with the broker in A2).
  Ask = bid + spread (bar spread in MT5 points × `point`).
- Session: NYSE regular session from the `exchange_calendars` XNYS calendar (America/New_York).
  Holidays → no session; early closes (e.g. 13:00 ET) → shorter session. DST handled by the calendar.
  The Baghdad offset is never hardcoded (16:30 Baghdad until 2026-10-30, 17:30 from 2026-11-02).
- Bars before the open or at/after the close are ignored. Bars must arrive strictly increasing.
- The strategy is a state machine that only sees bars after they close → no look-ahead
  (tested: wrecking every bar after the signal time does not change the signal).

## 2. Opening range (OR)
- OR window = first `opening_range_minutes` minutes after the open (default 15).
- OR high/low = max high / min low of those M1 bars, known at the close of the last one.
- Every minute of the window must be present, else `INCOMPLETE_OPENING_RANGE` (no trade that day).
- Optional width filters: `RANGE_TOO_NARROW` (< `min_range`), `RANGE_TOO_WIDE` (> `max_range`).

## 3. Breakout confirmation
- Confirmation candles of `confirm_tf_minutes` (default 5) are built from M1 bars, aligned to the OR end.
- A confirmation candle counts only when **all** its M1 bars exist. An incomplete candle is discarded
  (never merged into the next) and counted in `incomplete_confirm_candles`.
- Long breakout: candle **close** > OR high + `breakout_buffer`. Short: close < OR low − buffer.
  Intrabar pokes beyond the range do not count.
- The candle must close at or before open + `breakout_deadline_minutes` (default 90), else `NO_BREAKOUT`.
- First qualifying candle fixes the day's direction.

## 4. Retest
Window: from the breakout candle close for `retest_window_minutes` (default 30), on M1 bars.
For a long (short is the mirror image around OR low):
1. Swing low = lowest M1 low since the breakout close.
2. **False breakout:** an M1 close below OR high − `retest_max_penetration` → `FALSE_BREAKOUT`.
3. **Touch:** an M1 low ≤ OR high + `retest_tolerance`.
4. **Confirmation:** after (or on) the touch bar, an M1 close > OR high → signal at that bar's close.
5. Window ends without touch → `NO_RETEST`; touched but not confirmed → `RETEST_EXPIRED`.

## 5. Candidate (signal)
- Signal time = close of the confirming M1 bar. Must be ≤ `last_entry_at` =
  min(open + `last_entry_minutes`, close − `flat_before_close_minutes` − 1 min), else `ENTRY_WINDOW_CLOSED`.
- Long: entry_ref = close + spread (ask). Stop = swing low − `stop_buffer` (or OR low − buffer with
  `stop_method: range_opposite`). Stop is a **bid** level.
- Short: entry_ref = close (bid). Stop = swing high + spread + `stop_buffer` (an **ask** level).
- Risk distance = |entry_ref − stop| must lie in [`min_stop_distance`, `max_stop_distance`],
  else `STOP_DISTANCE_INVALID`. Target = entry_ref ± `reward_risk` × distance.
- At most **one signal per session**; after a signal the day is closed (no duplicates).
- Data gaps: more than `max_gap_minutes` consecutive missing minutes after the OR → `DATA_GAP`.

## 6. Risk checks (all run, all failures recorded; any one rejects)
`TRADING_DISABLED` (kill switch) · `REAL_TRADING_LOCKED` · `MODE_NOT_ALLOWED` · `NO_MARKET_DATA` ·
`STALE_DATA` (older than `max_data_age_seconds`, or timestamp in the future) ·
`SPREAD_LIMIT_NOT_CONFIGURED` · `SPREAD_UNKNOWN` · `SPREAD_TOO_WIDE` · `DAILY_LOSS_LIMIT` ·
`DAILY_LOSS_BUDGET_INSUFFICIENT` (this trade's loss at stop would exceed what is left of today's limit) ·
`TRADE_LIMIT` · `POSITION_LIMIT` · `INVALID_STOP` (wrong side / zero) · `SPEC_MISSING` · `SIZING_FAILED` ·
`MIN_LOT_EXCEEDS_RISK` · `NO_EQUITY`.

**Sizing:** risk money = equity × `risk_per_trade_pct` (code cap 2%).
Loss per lot = (distance + 2 × slippage) × tick_value / tick_size + 2 × commission per side.
Volume = floor(risk money / loss per lot) to `volume_step`, capped at volume max / `max_volume`.
Never rounded up. Below `volume_min` → `MIN_LOT_EXCEEDS_RISK` (the trade is skipped, the risk cap is not raised).
No martingale, no averaging down, no size increase after losses — none of these exist in the code.

## 7. Execution (paper broker)
- Entry: market order at the **open of the bar after the signal** (long pays ask + slippage, short
  receives bid − slippage). Missing next bar → `ENTRY_BAR_MISSING`. Open already beyond stop/target →
  `ENTRY_INVALID_AT_FILL`. Optional `max_entry_deviation` → `ENTRY_SLIPPED_TOO_FAR`.
- Long exits on bid; short exits on ask (bid + that bar's spread).
- Stop = market order: stop ∓ slippage; if the bar **opens beyond** the stop, filled at the open (worse).
- Target = limit order: filled exactly at the target, never better, even on a gap.
- **Stop and target inside the same bar → STOP** (order unknown; conservative), flagged `ambiguous_bar`.
- Time exit: at the open of the first bar at/after entry + `max_hold_minutes` (`TIME_EXIT`), or
  session close − `flat_before_close_minutes` if earlier (`SESSION_END`).
- Data ends with a position open → closed at the last bar's close (`DATA_END`).
- R multiple = net P&L ÷ planned loss at stop (incl. cost estimates). Slippage on the entry bar can
  make a stop-out worse than −1R; that is real and is reported, not hidden.
- MFE/MAE use exit-side prices; on the exit bar they include the whole bar range (upper bound).

## 8. Research protocol
- Chronological split of sessions: dev 60% / validation 20% / holdout 20%. Parameters are calibrated on
  dev only, frozen before validation; the holdout is run once (ledger in `data/holdout_ledger.jsonl`;
  reuse is refused unless forced, and the force is recorded).
- Every run stores its config hash, data source and full journal (taken AND rejected candidates).
- Each backtest is repeated at costs ×1, ×1.5 and ×2.
- No trades → `INSUFFICIENT DATA` with no performance figures. Fewer than 30 trades → `INSUFFICIENT_SAMPLE`.
