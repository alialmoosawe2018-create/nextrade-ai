"""Learning-loop routes: losses with their features, Claude's reviews (hypotheses), evidence-based
lessons (owner approves -> entry filter -> new strategy version), and Claude-idea tracking."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

import pandas as pd
from fastapi import FastAPI, HTTPException

from app.brokers.mt5_gateway import GatewayError, to_utc_bars
from app.research.features import FEATURE_DOCS
from app.research.learning import discover, idea_stats, loss_flags, resolve_idea
from app.services.chat import ChatUnavailable

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"reviews": {"type": "array", "items": {"type": "object", "properties": {
        "uid": {"type": "string"},
        "verdict": {"type": "string", "enum": ["VARIANCE", "PROCESS", "UNCLEAR"]},
        "factors": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"}}, "required": ["uid", "verdict", "factors", "explanation"]}}},
    "required": ["reviews"],
}

REVIEW_SYSTEM = """You review LOSING trades of a tested, rule-based strategy (Opening Range Breakout + Retest on
US indices). For each trade you get the features known AT ENTRY, the population percentiles (p20/p50/p80)
and the outcome. Classify:
- VARIANCE: the setup looked normal; markets are noisy and a loss is expected sometimes. This is the default.
- PROCESS: something measurable at entry was clearly unusual (e.g. spread large vs stop, against the trend,
  very late entry, very wide range) and plausibly caused the loss.
- UNCLEAR: not enough information.
Be honest: most losses are VARIANCE. Never use information from after the entry. Factors must be feature
names from the input. Explanations: one or two short sentences, in Iraqi Arabic, plain text."""


def register_learning(app: FastAPI, st, *, need_gateway: Callable, stop_monitor: Callable) -> None:
    review_jobs: dict[str, dict] = {}

    def symbol_or_400(symbol: str | None) -> str:
        sym = symbol or (st.spec.symbol if st.spec else None)
        if not sym:
            raise HTTPException(409, detail={"code": "NO_SYMBOL", "message": "Select a symbol or pass ?symbol="})
        return sym

    def latest_trades(symbol: str) -> tuple[str | None, pd.DataFrame]:
        run = st.db.latest_backtest_run(symbol)
        return run, (st.db.trades_df(run_id=run) if run else pd.DataFrame())

    @app.get("/api/learning/summary")
    def summary(symbol: str | None = None):
        sym = symbol or (st.spec.symbol if st.spec else None)
        run, df = latest_trades(sym) if sym else (None, pd.DataFrame())
        paper = st.db.trades_df(origin="paper")
        return {"symbol": sym, "latest_run": run, "backtest_trades": int(len(df)),
                "paper_trades": int(len(paper)), "lessons": st.db.lessons(),
                "ideas": idea_stats(st.db.ideas()), "feature_docs": FEATURE_DOCS,
                "adopted_filters": [f.model_dump() for f in st.settings.get("strategy").entry_filters]}

    @app.get("/api/learning/losses")
    def losses(symbol: str | None = None, limit: int = 40):
        sym = symbol_or_400(symbol)
        run, df = latest_trades(sym)
        if df.empty:
            return {"run_id": None, "losses": [], "note": "Run an analysis first: lessons are learned from its trades."}
        reviews = st.db.reviews()
        lost = df[df["r_multiple"] < 0].sort_values("entry_time", ascending=False).head(limit)
        cols = [c for c in FEATURE_DOCS if c in df.columns]
        out = []
        for _, t in lost.iterrows():
            out.append({"uid": t["uid"], "entry_time": t["entry_time"], "direction": t["direction"],
                        "exit_reason": t["exit_reason"], "r": t["r_multiple"], "mfe": t["mfe"], "mae": t["mae"],
                        "features": {c: (None if pd.isna(t[c]) else t[c]) for c in cols},
                        "flags": loss_flags(t, df), "review": reviews.get(t["uid"])})
        return {"run_id": run, "trades": int(len(df)), "losing": int((df["r_multiple"] < 0).sum()), "losses": out}

    @app.post("/api/learning/discover")
    def run_discover(symbol: str | None = None):
        sym = symbol_or_400(symbol)
        run, df = latest_trades(sym)
        if df.empty:
            raise HTTPException(409, detail={"code": "NO_TRADES", "message": "Run an analysis first."})
        result = discover(df, sym)
        if result["status"] == "OK":
            decided = {l["id"] for l in st.db.lessons() if l["status"] in ("ADOPTED", "REJECTED", "RETIRED")}
            st.db.replace_open_lessons(run, sym, [l for l in result["lessons"] if l["id"] not in decided])
        return {**result, "run_id": run}

    @app.post("/api/learning/lessons/{lid}/approve")
    def approve(lid: str):
        lesson = st.db.lesson(lid)
        if lesson is None:
            raise HTTPException(404, detail="no such lesson")
        if lesson["status"] != "VALIDATED":
            raise HTTPException(409, detail={"code": "NOT_VALIDATED",
                                             "message": "Only lessons that passed the validation set can be adopted."})
        strat = st.settings.get("strategy")
        filters = [f.model_dump() for f in strat.entry_filters if f.id != lid]
        filters.append({"id": lid, "feature": lesson["feature"], "op": lesson["op"], "value": lesson["value"],
                        "note": f"adopted {datetime.now(timezone.utc):%Y-%m-%d} from run {lesson['run_id']}"})
        st.settings.put("strategy", {**strat.model_dump(mode="json"), "entry_filters": filters})
        st.db.set_lesson_status(lid, "ADOPTED")
        stop_monitor()
        return {"adopted": lid, "entry_filters": filters,
                "next": "Re-run the analysis (validation split) to see the new version's results."}

    @app.post("/api/learning/lessons/{lid}/reject")
    def reject(lid: str):
        if st.db.lesson(lid) is None:
            raise HTTPException(404, detail="no such lesson")
        st.db.set_lesson_status(lid, "REJECTED")
        return {"rejected": lid}

    @app.post("/api/learning/lessons/{lid}/retire")
    def retire(lid: str):
        strat = st.settings.get("strategy")
        filters = [f.model_dump() for f in strat.entry_filters if f.id != lid]
        st.settings.put("strategy", {**strat.model_dump(mode="json"), "entry_filters": filters})
        if st.db.lesson(lid):
            st.db.set_lesson_status(lid, "RETIRED")
        stop_monitor()
        return {"retired": lid, "entry_filters": filters}

    # ------------------------------------------------------------ Claude reviews
    @app.post("/api/learning/review")
    def review(symbol: str | None = None):
        sym = symbol_or_400(symbol)
        run, df = latest_trades(sym)
        if df.empty:
            raise HTTPException(409, detail={"code": "NO_TRADES", "message": "Run an analysis first."})
        done = st.db.reviews()
        todo = df[(df["r_multiple"] < 0) & (~df["uid"].isin(list(done)))].sort_values("entry_time", ascending=False).head(12)
        if todo.empty:
            return {"job_id": None, "status": "DONE", "reviewed": 0, "note": "All losses already reviewed."}
        cols = [c for c in FEATURE_DOCS if c in df.columns]
        pct = {c: {q: round(float(pd.to_numeric(df[c], errors="coerce").quantile(q)), 4)
                   for q in (0.2, 0.5, 0.8)} for c in cols if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 10}
        items = [{"uid": t["uid"], "direction": t["direction"], "entry_time": t["entry_time"], "exit_reason": t["exit_reason"],
                  "r": round(float(t["r_multiple"]), 3), "mfe": t["mfe"], "mae": t["mae"],
                  "features": {c: (None if pd.isna(t[c]) else t[c]) for c in cols}} for _, t in todo.iterrows()]
        prompt = json.dumps({"feature_meaning": FEATURE_DOCS, "population_percentiles": pct, "trades": items},
                            ensure_ascii=False, default=str)
        job_id = uuid.uuid4().hex[:12]
        job = {"job_id": job_id, "status": "RUNNING", "reviewed": 0, "error": None}
        review_jobs[job_id] = job

        def work():
            try:
                data = st.chat.runner.run_json(REVIEW_SYSTEM, prompt, REVIEW_SCHEMA)["data"]
                reviews = data.get("reviews") or []
                valid_uids = {i["uid"] for i in items}
                for r in reviews:
                    if r.get("uid") in valid_uids and r.get("verdict") in ("VARIANCE", "PROCESS", "UNCLEAR"):
                        factors = [f for f in r.get("factors", []) if f in FEATURE_DOCS]
                        st.db.add_review(r["uid"], r["verdict"], factors, str(r.get("explanation", ""))[:500],
                                         getattr(st.chat.runner, "model", "?"))
                        job["reviewed"] += 1
                job["status"] = "DONE"
            except ChatUnavailable as e:
                job["status"], job["error"] = "FAILED", {"code": e.code, "message": str(e)}
            except Exception as e:  # noqa: BLE001
                job["status"], job["error"] = "FAILED", {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"}

        threading.Thread(target=work, daemon=True, name=f"review-{job_id}").start()
        return {"job_id": job_id, "status": "RUNNING", "count": len(items)}

    @app.get("/api/learning/review/{job_id}")
    def review_job(job_id: str):
        job = review_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, detail="no such job")
        return job

    # ------------------------------------------------------ Claude bias record
    @app.get("/api/learning/biases")
    def biases():
        """Each pre-open bias vs what US100 actually did open->close. Late biases (after the open) are excluded."""
        from app.brokers.assets import BY_KEY, resolve
        from app.core.clock import MarketCalendar
        rows = st.db.biases("US100")
        gw = st.gateway
        sym = None
        if gw is not None and gw.connected:
            sym = resolve(BY_KEY["US100"], gw.find_symbols(nasdaq_only=False))
        cal = MarketCalendar()
        out, hits = [], []
        for b in rows:
            sess = cal.session(pd.Timestamp(b["day"]).date())
            item = {**b, "pre_open": sess is not None and pd.Timestamp(b["created_at"]) < sess.open_utc,
                    "move_pct": None, "hit": None}
            if sess is not None and sym and st.profile and st.profile.server_tz and pd.Timestamp(st.clock()) > sess.close_utc:
                bars = to_utc_bars(gw.rates_m1_server(sym, sess.open_utc, sess.close_utc), st.profile.server_tz.to_tz())
                d = bars[(bars["time"] >= sess.open_utc) & (bars["time"] < sess.close_utc)]
                if len(d) > 300:
                    item["move_pct"] = round((float(d["close"].iloc[-1]) / float(d["open"].iloc[0]) - 1) * 100, 3)
                    if b["bias"] != "NEUTRAL" and item["pre_open"]:
                        item["hit"] = (item["move_pct"] > 0) == (b["bias"] == "BULLISH")
                        hits.append(item["hit"])
            out.append(item)
        n = len(hits)
        stats = {"scored": n, "hit_rate": round(sum(hits) / n, 3) if n else None,
                 "note": "coin-flip is 50%; needs 30+ scored days to mean anything" if n < 30 else None}
        return {"biases": out, "stats": stats}

    # --------------------------------------------------------- idea tracking
    @app.get("/api/learning/ideas")
    def ideas():
        ideas = st.db.ideas()
        gw = st.gateway
        if gw is not None and gw.connected and st.profile and st.profile.server_tz:
            tz = st.profile.server_tz.to_tz()
            for i in ideas:
                if i.get("outcome") in ("STOP", "TARGET", "TIMEOUT"):
                    continue
                try:
                    start = pd.Timestamp(i["created_at"])
                    raw = gw.rates_m1_server(i["symbol"], start, pd.Timestamp(st.clock()))
                    bars = to_utc_bars(raw, tz)
                    outcome, r = resolve_idea(i, bars, float(i.get("spread") or 0))
                    if outcome != "OPEN":
                        st.db.set_idea_outcome(i["id"], outcome, r)
                        i["outcome"], i["outcome_r"] = outcome, r
                except GatewayError:
                    continue
        return {"ideas": ideas, "stats": idea_stats(ideas)}
