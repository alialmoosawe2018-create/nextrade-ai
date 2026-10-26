"""Chat, trade ideas, kill switch and multi-asset watchlist routes.

Nothing here sends an order. Trade ideas are risk-checked proposals for MANUAL execution.
"""
from __future__ import annotations

import json
import os
import threading
import time as time_mod
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.brokers.assets import ASSETS, BY_KEY, mentioned, resolve
from app.brokers.mt5_gateway import GatewayError
from app.core.clock import BAGHDAD, NEW_YORK
from app.services.chat import (OUTPUT_SCHEMA, SYSTEM_PROMPT, ChatService, ChatUnavailable, ClaudeCliRunner,
                               find_claude_exe)
from app.research.learning import idea_stats
from app.services.ideas import IdeaRejected, propose
import pandas as pd

from app.market.news import news_block
from app.services.market_context import snapshot


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ActionFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def register_assistant(app: FastAPI, st, *, need_gateway: Callable, need_symbol: Callable, start_live: Callable,
                       start_analysis: Callable, stop_monitor: Callable) -> None:
    # ------------------------------------------------------------ helpers
    def broker_symbols() -> list[dict]:
        return need_gateway().find_symbols(nasdaq_only=False)   # rows with name + path (stocks excluded by resolve)

    def asset_symbol(key: str) -> str:
        a = BY_KEY.get((key or "").upper())
        if a is None:
            raise ActionFailed("UNKNOWN_ASSET", f"unknown asset {key!r}")
        sym = resolve(a, broker_symbols())
        if sym is None:
            raise ActionFailed("ASSET_NOT_AT_BROKER", f"{a.name_en} is not offered by this broker/account")
        return sym

    def kill() -> dict:
        risk = st.settings.get("risk")
        st.settings.put("risk", {**risk.model_dump(mode="json"), "trading_enabled": False})
        stop_monitor()
        cancelled = 0
        for idea in st.ideas.values():
            if idea["status"] == "PROPOSED":
                idea["status"] = "CANCELLED"
                cancelled += 1
        return {"trading_enabled": False, "live_monitor": "STOPPED", "ideas_cancelled": cancelled}

    def tz_or_none():
        return st.profile.server_tz.to_tz() if st.profile and st.profile.server_tz else None

    def learning_memory() -> dict:
        """What the platform has LEARNED, with evidence — so the model does not repeat proven mistakes."""
        lessons = st.db.lessons()
        reviews = list(st.db.reviews().values())
        verdicts: dict[str, int] = {}
        for r in reviews:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        ideas = st.db.ideas()
        return {
            "adopted_rules": [{"rule": f"skip when {l['feature']} {l['op']} {l['value']}", "symbol": l["symbol"],
                               "validation": l["evidence"].get("validation")} for l in lessons if l["status"] == "ADOPTED"],
            "validated_waiting_owner": [f"{l['feature']} {l['op']} {l['value']}" for l in lessons if l["status"] == "VALIDATED"],
            "rejected_or_unproven": [f"{l['feature']} {l['op']} {l['value']} ({l['status']})" for l in lessons
                                     if l["status"] in ("REJECTED", "UNVALIDATED", "RETIRED")][:10],
            "loss_reviews": {"count": len(reviews), "by_verdict": verdicts,
                             "latest": [{"verdict": r["verdict"], "factors": r["factors"], "why": r["explanation"]}
                                        for r in reviews[-5:]]},
            "your_trade_ideas_track_record": idea_stats(ideas),
            "how_to_use": "Respect adopted rules when proposing trades. Mention your real track record when "
                          "confidence matters. Do not present unproven patterns as lessons.",
        }

    def news_view(hours_ahead: int = 36) -> dict:
        events = st.news.events()
        if events is None:
            return {"available": False, "note": "economic calendar unavailable (no internet / feed down)"}
        now = pd.Timestamp(st.clock())
        risk = st.settings.get("risk")
        soon = [e for e in events if now - pd.Timedelta(hours=2) <= e["time_utc"] <= now + pd.Timedelta(hours=hours_ahead)
                and e["impact"] in ("High", "Medium")]
        return {"available": True, "covers": "current week",
                "us100_blocked_now": news_block(events, now, risk),
                "upcoming": [{"title": e["title"], "currency": e["country"], "impact": e["impact"],
                              "baghdad": e["time_utc"].tz_convert(BAGHDAD).strftime("%a %H:%M"),
                              "utc": e["time_utc"].strftime("%Y-%m-%d %H:%M"),
                              "forecast": e["forecast"], "previous": e["previous"]} for e in soon][:25]}

    def context(message: str) -> dict:
        now = st.clock()
        ctx: dict = {
            "now_utc": now.isoformat(timespec="seconds"),
            "now_baghdad": now.astimezone(BAGHDAD).strftime("%Y-%m-%d %H:%M"),
            "now_new_york": now.astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M"),
            "execution": "NO ORDERS ARE SENT BY THE PLATFORM. propose_trade creates a risk-checked idea "
                         "that the owner may enter by hand in MT5.",
        }
        gw = st.gateway
        if gw is None or not gw.connected:
            ctx["connection"] = {"connected": False}
            return ctx
        info = gw.info
        ctx["connection"] = {"connected": True, "source": info.source, "account_mode": info.account_mode,
                             "company": info.company, "currency": info.currency, "equity": info.equity,
                             "read_only": info.read_only}
        risk = st.settings.get("risk")
        ctx["risk_settings"] = {"risk_per_trade_pct": risk.risk_per_trade_pct, "max_daily_loss_pct": risk.max_daily_loss_pct,
                                "kill_switch_on": not risk.trading_enabled}
        ctx["selected_symbol"] = st.spec.symbol if st.spec else None
        if st.monitor is not None:
            snap = st.monitor.snapshot()
            ctx["paper_live_monitor"] = {k: snap.get(k) for k in ("status", "symbol", "phase", "or_high", "or_low",
                                                                  "direction", "paper_balance", "data_age_seconds")}
            ctx["paper_live_monitor"]["paper_trades_today"] = len(snap.get("trades") or [])
        # Watchlist quotes for every asset the broker offers.
        syms = broker_symbols()
        wl = []
        for a in ASSETS:
            sym = resolve(a, syms)
            q = gw.quote(sym) if sym else None
            wl.append({"asset": a.key, "symbol": sym, "bid": q["bid"] if q else None, "ask": q["ask"] if q else None,
                       "spread": q["spread"] if q else None})
        ctx["watchlist"] = wl
        # Detailed snapshots for the assets named in the message (max 2), else the selected symbol.
        targets = [a for a in mentioned(message)][:2]
        detail = []
        for a in targets:
            sym = resolve(a, syms)
            if sym:
                detail.append(snapshot(gw, sym, tz_or_none(), now, info.source))
            else:
                detail.append({"asset": a.key, "error": "not offered by this broker"})
        if not targets and st.spec is not None:
            detail.append(snapshot(gw, st.spec.symbol, tz_or_none(), now, info.source))
        ctx["market"] = detail
        ctx["recent_ideas"] = list(st.ideas.values())[-5:]
        ctx["learning_memory"] = learning_memory()
        ctx["economic_calendar"] = news_view()
        try:
            runs = sorted((st.data_dir / "runs").glob("*"), reverse=True)[:3]
            ctx["recent_backtests"] = [r.name for r in runs]
        except OSError:
            pass
        return ctx

    def execute(action: dict) -> dict:
        t = action["type"]
        try:
            if t == "kill_switch":
                return kill()
            if t == "stop_live":
                stop_monitor()
                return {"live_monitor": "STOPPED"}
            if t == "start_live":
                snap = start_live()
                return {"live_monitor": snap.get("status"), "symbol": snap.get("symbol")}
            if t == "select_symbol":
                sym = asset_symbol(action.get("asset", ""))
                spec, raw = need_gateway().instrument_spec(sym)
                stop_monitor()
                st.spec, st.spec_raw = spec, raw
                st.profile.symbol = sym
                st.profiles.save(st.profile)
                return {"selected": sym}
            if t == "run_backtest":
                sym = asset_symbol(action.get("asset", "")) if action.get("asset") else need_symbol()[1].symbol
                gw = need_gateway()
                spec, _ = gw.instrument_spec(sym)
                end = date.fromisoformat(action["end"]) if action.get("end") else (st.clock().date() - timedelta(days=1))
                start = date.fromisoformat(action["start"]) if action.get("start") else end - timedelta(days=365)
                return {"symbol": sym, **start_analysis(gw, spec, start, end, action.get("split") or "dev")}
            if t == "propose_trade":
                sym = asset_symbol(action["asset"])
                idea = propose(need_gateway(), sym, action["side"], float(action["sl"]),
                               float(action["tp"]) if action.get("tp") is not None else None, action.get("risk_pct"),
                               st.settings.get("risk"), st.settings.get("costs"), action.get("rationale") or "")
                st.ideas[idea.id] = idea.to_dict()
                st.db.add_idea(idea.to_dict())
                return idea.to_dict()
        except HTTPException as e:
            d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            raise ActionFailed(d.get("code", f"HTTP_{e.status_code}"), d.get("message") or str(d)) from None
        except IdeaRejected as e:
            raise ActionFailed(e.code, str(e)) from None
        except GatewayError as e:
            raise ActionFailed("GATEWAY_ERROR", str(e)) from None
        raise ActionFailed("UNKNOWN_ACTION", t)

    st.chat = ChatService(ClaudeCliRunner(st.data_dir / "chat" / "sandbox"), st.data_dir / "chat" / "history.jsonl",
                          context_fn=context, execute=execute, kill=kill)

    BRIEF_INSTRUCTION = ("Write today's brief for the owner (Iraqi Arabic, plain text, short): 1) today's important "
                         "economic news with Baghdad times and what they usually do to US indices/gold/USD, 2) the state "
                         "of US100, US30, gold and oil from CONTEXT (trend, levels, volatility), 3) whether today looks "
                         "calm or dangerous for the opening-range strategy and why, 4) what to watch. Facts from CONTEXT only. "
                         "No trade ideas, no actions.")
    brief_state = {"day": None, "running": False}
    BIAS_SCHEMA = {"type": "object", "properties": {
        "bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasons": {"type": "array", "items": {"type": "string"}}}, "required": ["bias", "confidence", "reasons"]}
    BIAS_INSTRUCTION = ("Give today's FUNDAMENTAL bias for US100 (Nasdaq 100) for the US cash session, from the "
                        "economic calendar and the macro facts in CONTEXT (S&P/dollar/gold moves, trend, news timing). "
                        "NEUTRAL when there is no clear edge. Confidence 0-100 must be honest (most days 40-60). "
                        "Reasons: 2-4 short points in Iraqi Arabic. This is recorded to be scored later, not traded.")

    def record_bias(ctx: dict) -> dict | None:
        """Shadow only: stored with its timestamp; it influences nothing until proven by the learning loop."""
        out = st.chat.runner.run_json(SYSTEM_PROMPT, "CONTEXT (facts from the platform, JSON):\n"
                                      + json.dumps(ctx, ensure_ascii=False, default=str) + "\n\nTASK:\n" + BIAS_INSTRUCTION,
                                      BIAS_SCHEMA)["data"]
        if out.get("bias") not in ("BULLISH", "BEARISH", "NEUTRAL"):
            return None
        day = datetime.now(NEW_YORK).date().isoformat()
        st.db.set_bias(day, "US100", out["bias"], max(0, min(100, int(out.get("confidence", 50)))),
                       [str(r)[:200] for r in out.get("reasons", [])][:4])
        return st.db.bias(day, "US100")

    def run_brief() -> dict:
        if brief_state["running"]:
            return {"status": "RUNNING"}
        brief_state["running"] = True
        try:
            ctx = context("US100 US30 gold oil")
            out = st.chat.runner.run(SYSTEM_PROMPT, "CONTEXT (facts from the platform, JSON):\n"
                                     + json.dumps(ctx, ensure_ascii=False, default=str)
                                     + "\n\nTASK:\n" + BRIEF_INSTRUCTION, OUTPUT_SCHEMA)
            msg = st.chat._append(ChatService._msg("assistant", out["reply"], brief=True,
                                                   data_source=ctx.get("connection", {}).get("source")))
            brief_state["day"] = datetime.now(BAGHDAD).date().isoformat()
            bias = None
            try:
                bias = record_bias(ctx)
            except ChatUnavailable:
                pass
            return {"status": "DONE", "message": msg, "bias": bias}
        except ChatUnavailable as e:
            return {"status": "FAILED", "error": {"code": e.code, "message": str(e)}}
        finally:
            brief_state["running"] = False

    def brief_scheduler() -> None:
        """Once a day at/after 10:00 Baghdad, while the platform runs and an account is connected."""
        while True:
            try:
                now = datetime.now(BAGHDAD)
                if (now.hour >= 10 and brief_state["day"] != now.date().isoformat()
                        and st.gateway is not None and st.gateway.connected and find_claude_exe()):
                    run_brief()
            except Exception as e:  # noqa: BLE001
                st.record_error("brief", f"{type(e).__name__}: {e}")
            time_mod.sleep(60)

    if os.environ.get("NEXTRADE_NO_BACKGROUND") != "1":
        threading.Thread(target=brief_scheduler, daemon=True, name="daily-brief").start()

    @app.get("/api/news")
    def news():
        return {**st.news.status(), **news_view(hours_ahead=24 * 7)}

    @app.post("/api/brief")
    def brief():
        return run_brief()

    # ------------------------------------------------------------- routes
    @app.post("/api/kill")
    def kill_route():
        return kill()

    @app.post("/api/kill/release")
    def release():
        risk = st.settings.get("risk")
        st.settings.put("risk", {**risk.model_dump(mode="json"), "trading_enabled": True})
        return {"trading_enabled": True}

    @app.get("/api/watchlist")
    def watchlist():
        gw = need_gateway()
        syms = broker_symbols()
        out = []
        for a in ASSETS:
            sym = resolve(a, syms)
            q = gw.quote(sym) if sym else None
            out.append({"asset": a.key, "name_ar": a.name_ar, "name_en": a.name_en, "symbol": sym,
                        "bid": q["bid"] if q else None, "ask": q["ask"] if q else None,
                        "spread": q["spread"] if q else None, "source": gw.info.source})
        return out

    @app.get("/api/ideas")
    def ideas():
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for idea in st.ideas.values():
            if idea["status"] == "PROPOSED" and idea["valid_until"] < now:
                idea["status"] = "EXPIRED"
        return list(reversed(list(st.ideas.values())))

    @app.post("/api/ideas/{idea_id}/dismiss")
    def dismiss(idea_id: str):
        idea = st.ideas.get(idea_id)
        if idea is None:
            raise HTTPException(404, detail="no such idea")
        idea["status"] = "DISMISSED"
        return idea

    @app.get("/api/chat/status")
    def chat_status():
        return {"cli_found": find_claude_exe() is not None, "model": st.chat.runner.model,
                "note": "Uses the Claude Code login on this computer (run `claude` then /login once)."}

    @app.get("/api/chat/history")
    def chat_history():
        return st.chat.history(200)

    @app.post("/api/chat/clear")
    def chat_clear():
        st.chat.clear()
        return {"cleared": True}

    @app.post("/api/chat")
    def chat(body: ChatIn):
        try:
            return st.chat.submit(body.message)
        except RuntimeError as e:
            if str(e) == "CHAT_BUSY":
                raise HTTPException(409, detail={"code": "CHAT_BUSY", "message": "Claude is still answering"}) from None
            raise

    @app.get("/api/chat/jobs/{job_id}")
    def chat_job(job_id: str):
        job = st.chat.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, detail="no such job")
        return job


__all__ = ["register_assistant", "ChatUnavailable"]
