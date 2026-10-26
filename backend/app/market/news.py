"""Economic calendar (this week) — for news blackouts, the daily brief and the chat.

Source: the public weekly JSON feed at nfs.faireconomy.media (Forex Factory calendar data). It covers the
CURRENT WEEK only, so news protection applies to live monitoring and chat, not to historical backtests.
The feed is cached under data/news/ and fetched at most once an hour; when it cannot be fetched the last
cache is used, and if there is none the platform says "news unavailable" instead of pretending all is clear.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.clock import NEW_YORK

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
REFRESH_SECONDS = 3600
RETRY_AFTER_FAILURE_SECONDS = 600   # never hammer the feed (it answers 429 to bursts)


class NewsCalendar:
    def __init__(self, cache_dir: Path, fetcher=None) -> None:
        self.cache = Path(cache_dir) / "calendar_thisweek.json"
        self._fetch = fetcher or self._http_fetch
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self._last_attempt = 0.0

    @staticmethod
    def _http_fetch() -> list:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "NexTrade-AI/0.3 (private, local)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            fresh = self.cache.exists() and time.time() - self.cache.stat().st_mtime < REFRESH_SECONDS
            if fresh and not force:
                return
            if not force and time.time() - self._last_attempt < (RETRY_AFTER_FAILURE_SECONDS if self.last_error else 60):
                return
            self._last_attempt = time.time()
            try:
                data = self._fetch()
                if not isinstance(data, list):
                    raise ValueError("unexpected feed format")
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.last_error = None
            except Exception as e:  # noqa: BLE001 — keep the last good cache
                self.last_error = f"{type(e).__name__}: {e}"

    def events(self) -> list[dict] | None:
        """All events with a UTC timestamp, or None when no calendar is available at all."""
        self.refresh()
        if not self.cache.exists():
            return None
        out = []
        for e in json.loads(self.cache.read_text(encoding="utf-8")):
            try:
                t = pd.Timestamp(e["date"]).tz_convert("UTC")
            except (KeyError, ValueError, TypeError):
                continue
            out.append({"title": e.get("title", ""), "country": e.get("country", ""), "impact": e.get("impact", ""),
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", ""), "time_utc": t})
        return sorted(out, key=lambda x: x["time_utc"])

    def status(self) -> dict:
        cached = self.cache.exists()
        return {"available": cached, "source": FEED_URL, "covers": "current week only",
                "cached_at": datetime.fromtimestamp(self.cache.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
                if cached else None, "last_error": self.last_error}


def relevant(events: list[dict], impacts, currencies) -> list[dict]:
    return [e for e in events if e["impact"] in impacts and e["country"] in currencies]


def news_block(events: list[dict] | None, now: pd.Timestamp, risk) -> str | None:
    """Reason to block a trade at `now`, or None. Unknown calendar never blocks (it is reported instead)."""
    if not events:
        return None
    rel = relevant(events, risk.news_impacts, risk.news_currencies)
    ny_day = now.tz_convert(NEW_YORK).date()
    for e in rel:
        if any(k.lower() in e["title"].lower() for k in risk.news_skip_day_titles) and \
                e["time_utc"].tz_convert(NEW_YORK).date() == ny_day:
            return f"SKIP_DAY: {e['title']} {e['time_utc']:%H:%M}Z"
    before = pd.Timedelta(minutes=risk.news_blackout_before_min)
    after = pd.Timedelta(minutes=risk.news_blackout_after_min)
    for e in rel:
        if e["time_utc"] - before <= now <= e["time_utc"] + after:
            return f"{e['title']} {e['time_utc']:%H:%M}Z"
    return None
