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
    symbol_patterns: tuple[str, ...]      # base symbol names, priority order (see resolve)
    words: tuple[str, ...]                # how the owner may refer to it in chat
    substitutes: tuple[str, ...] = ()     # a DIFFERENT instrument used only when none of the bases exist


# symbol_patterns = base names in PRIORITY order; a broker symbol matches a base exactly or with a short
# suffix (".m", "-ECN", "M", "pro"). Stocks and ETFs are never chosen (see resolve).
ASSETS: tuple[Asset, ...] = (
    Asset("US100", "ناسداك 100", "Nasdaq 100",
          ("US100", "USTEC", "NAS100", "USTECH100", "NASDAQ100", "USTECH", "NDX100", "NQ100"),
          ("us100", "nas100", "ناسداك", "نازداك", "nasdaq", "ustec")),
    Asset("US30", "داو جونز 30", "Dow Jones 30",
          ("US30", "DJ30", "WS30", "DJI30", "USA30", "DOW30", "DJI"), ("us30", "داو", "الداو", "dow", "dj30")),
    Asset("US500", "إس آند بي 500", "S&P 500",
          ("US500", "SPX500", "SP500", "USSPX500", "SPX"), ("us500", "spx", "s&p", "sp500")),
    Asset("XAUUSD", "الذهب", "Gold",
          ("XAUUSD", "GOLD"), ("xauusd", "gold", "ذهب", "الذهب", "دهب", "الدهب")),
    Asset("OIL", "النفط الخام", "Crude oil",
          ("USOIL", "WTI", "XTIUSD", "USCRUDE", "CRUDEOIL"),
          ("oil", "wti", "brent", "نفط", "النفط", "بترول", "usoil"), ("UKOIL", "BRENT", "XBRUSD")),
    Asset("EURUSD", "يورو/دولار", "EUR/USD", ("EURUSD",), ("eurusd", "يورو", "euro")),
    Asset("GBPUSD", "باوند/دولار", "GBP/USD", ("GBPUSD",), ("gbpusd", "باوند", "استرليني", "pound")),
    Asset("USDJPY", "دولار/ين", "USD/JPY", ("USDJPY",), ("usdjpy", "الين", "yen", "ين ياباني")),
)
BY_KEY = {a.key: a for a in ASSETS}
_SUFFIX = r"(?:[._\-]?[A-Za-z0-9]{0,4})"
# Company securities: stock/ETF/warrant folders, and anything filed under an exchange (MetaQuotes: "Nasdaq\Other").
_EQUITY_PATH = re.compile(r"(^|[\\/])(stocks?|shares?|etfs?|etns?|equit\w*|warrants?|rights?|bonds?)([\\/]|$)"
                          r"|^(nasdaq|nyse|amex|arca|lse|xetra|euronext)[\\/]", re.I)


def resolve(asset: Asset, broker_symbols: list) -> str | None:
    """Best broker symbol for an asset. `broker_symbols` = names, or dicts with name/path from find_symbols().

    A stock/ETF is NEVER chosen ("WTI" is also W&T Offshore's ticker, "GOLD" Barrick's): an asset with only
    equity matches is reported as not offered. Ranking: never a substitute instrument (Brent for WTI)
    when a base exists; then an exact base name over a suffixed one (the unsuffixed symbol is the broker's
    main contract); then base-name priority; then shortest.
    """
    bases = [(False, b) for b in asset.symbol_patterns] + [(True, b) for b in asset.substitutes]
    cands = []
    for item in broker_symbols:
        name = item["name"] if isinstance(item, dict) else item
        path = item.get("path", "") if isinstance(item, dict) else ""
        if _EQUITY_PATH.search(path or ""):
            continue
        for prio, (sub, base) in enumerate(bases):
            if re.fullmatch(re.escape(base) + _SUFFIX, name, re.I):
                cands.append((sub, name.upper() != base, prio, len(name), name))
                break
    if not cands:
        return None
    return sorted(cands)[0][-1]


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
