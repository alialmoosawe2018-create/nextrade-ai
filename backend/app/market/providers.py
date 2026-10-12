"""Market-data providers. Each carries an explicit DataSource label.

- MockProvider: synthetic random-walk bars. Labelled MOCK. For tests and the
  pipeline demo ONLY — any result computed from it is not market evidence.
- ParquetProvider: historical M1 bars saved from the broker (A2). Labelled HISTORICAL.
- The MT5 provider (read-only) arrives in phase A2.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.core.models import DataSource, Session

BAR_COLUMNS = ["time", "open", "high", "low", "close", "spread"]


class MockProvider:
    source = DataSource.MOCK

    def __init__(self, seed: int = 7, start_price: float = 20000.0, vol_per_min: float = 6.0,
                 spread_points: int = 150) -> None:
        self.seed = seed
        self.start_price = start_price
        self.vol = vol_per_min
        self.spread_points = spread_points

    def bars(self, sessions: list[Session]) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        frames = []
        price = self.start_price
        for s in sessions:
            n = int((s.close_utc - s.open_utc) / pd.Timedelta(minutes=1))
            times = pd.date_range(s.open_utc, periods=n, freq="1min")
            steps = rng.normal(0.0, self.vol, size=n)
            closes = price + np.cumsum(steps)
            opens = np.concatenate([[price], closes[:-1]])
            wick = np.abs(rng.normal(0.0, self.vol * 0.5, size=(2, n)))
            highs = np.maximum(opens, closes) + wick[0]
            lows = np.minimum(opens, closes) - wick[1]
            spread = rng.integers(self.spread_points // 2, self.spread_points * 2, size=n)
            frames.append(pd.DataFrame({"time": times, "open": opens, "high": highs, "low": lows,
                                        "close": closes, "spread": spread}))
            price = closes[-1] + rng.normal(0.0, self.vol * 5)  # overnight gap
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)


class ParquetProvider:
    source = DataSource.HISTORICAL

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def bars(self) -> pd.DataFrame:
        df = pd.read_parquet(self.path)
        missing = [c for c in BAR_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{self.path}: missing columns {missing}")
        df = df[BAR_COLUMNS].copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df
