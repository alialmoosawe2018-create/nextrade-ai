"""Broker-neutral asset catalogue: maps what the owner says (Arabic/English) to the broker's symbol.

Brokers name the same market differently (US100 / NAS100 / USTEC, XAUUSD / GOLD, USOIL / WTI / XTIUSD)
and add suffixes (.m, .pro, -ecn). Nothing is assumed: candidates are matched against the symbol list
the connected terminal actually returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    key: str
    name_ar: str
    name_en: str
    symbol_patterns: tuple[str, ...]      # regex on the broker symbol name (case-insensitive)
    words: tuple[str, ...]                # how the owner may refer to it in chat


ASSETS: tuple[Asset, ...] = (
    Asset("US100", "ناسداك 100", "Nasdaq 100",
          (r"^(US ?100|USTEC|US ?TECH|NAS ?100|NASDAQ ?100|NDX|NQ)",), ("us100", "nas100", "ناسداك", "نازداك", "nasdaq", "ustec")),
    Asset("US30", "داو جونز 30", "Dow Jones 30",
          (r"^(US ?30|DJ ?30|WS ?30|DOW|DJI|YM)",), ("us30", "داو", "الداو", "dow", "dj30")),
    Asset("XAUUSD", "الذهب", "Gold",
          (r"^(XAUUSD|GOLD)",), ("xauusd", "gold", "ذهب", "الذهب", "دهب", "الدهب")),
    Asset("OIL", "النفط الخام", "Crude oil",
          (r"^(USOIL|WTI|XTIUSD|CL|OIL|CRUDE|UKOIL|BRENT|XBRUSD)",), ("oil", "wti", "brent", "نفط", "النفط", "بترول", "usoil")),
    Asset("EURUSD", "يورو/دولار", "EUR/USD", (r"^EURUSD",), ("eurusd", "يورو", "euro")),
    Asset("GBPUSD", "باوند/دولار", "GBP/USD", (r"^GBPUSD",), ("gbpusd", "باوند", "استرليني", "pound")),
    Asset("USDJPY", "دولار/ين", "USD/JPY", (r"^USDJPY",), ("usdjpy", "الين", "yen", "ين ياباني")),
)
BY_KEY = {a.key: a for a in ASSETS}


def resolve(asset: Asset, broker_symbols: list[str]) -> str | None:
    """Best broker symbol for an asset: pattern match, shortest name first (fewest suffixes)."""
    hits = [s for s in broker_symbols for p in asset.symbol_patterns if re.match(p, s, re.I)]
    if asset.key == "OIL":  # prefer WTI over Brent when both exist
        wti = [s for s in hits if not re.match(r"^(UKOIL|BRENT|XBRUSD)", s, re.I)]
        hits = wti or hits
    return sorted(set(hits), key=lambda s: (len(s), s))[0] if hits else None


# A regex word boundary does not work next to Arabic letters, so word edges are explicit.
# Arabic attaches prefixes to words (and / with / for / so / like + the): those are allowed,
# but nothing else may touch the word, so the yen word never matches inside a longer word.
_LETTER = r"0-9A-Za-z؀-ۿ"
_EDGE_L = r"(?<![" + _LETTER + r"])(?:[وبلفك])?"
_EDGE_R = r"(?![" + _LETTER + r"])"


def mentioned(text: str) -> list[Asset]:
    """Assets the owner referred to in a chat message (order of first mention), whole words only."""
    found = []
    for a in ASSETS:
        pos = [m.start() for w in a.words
               for m in [re.search(_EDGE_L + re.escape(w) + _EDGE_R, text, re.I)] if m]
        if pos:
            found.append((min(pos), a))
    return [a for _, a in sorted(found, key=lambda x: x[0])]
