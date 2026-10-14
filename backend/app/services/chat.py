"""In-platform chat with Claude (owner request 2026-10-12).

Model access: the locally installed Claude Code CLI, headless, under the owner's own Claude login
(no API key in this app). The model runs with NO tools, no MCP servers, no session persistence,
in an empty working folder, and must answer in a strict JSON schema: {reply, actions[]}.

The model cannot do anything by itself. Its `actions` are requests that this code validates and
executes through the same safe paths as the UI: start/stop the paper live monitor, kill switch,
background analysis, and TRADE IDEAS (risk-checked, executed by the owner by hand in MT5 — the
platform sends no orders). A stop command is handled deterministically, without the model.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ACTION_TYPES = ("start_live", "stop_live", "kill_switch", "run_backtest", "propose_trade", "select_symbol")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Answer to the owner, in the owner's language (usually Iraqi Arabic)."},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ACTION_TYPES)},
                    "asset": {"type": "string", "description": "Asset key: US100, US30, XAUUSD, OIL, EURUSD, GBPUSD, USDJPY"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "sl": {"type": "number"},
                    "tp": {"type": "number"},
                    "risk_pct": {"type": "number"},
                    "start": {"type": "string", "description": "YYYY-MM-DD"},
                    "end": {"type": "string", "description": "YYYY-MM-DD"},
                    "split": {"type": "string", "enum": ["dev", "validation", "all"]},
                    "rationale": {"type": "string"},
                },
                "required": ["type"],
            },
        },
    },
    "required": ["reply", "actions"],
}

SYSTEM_PROMPT = """You are the assistant inside NexTrade AI, the owner's PRIVATE trading research platform.
The owner writes in Iraqi Arabic (sometimes English). Reply in the owner's language, short and clear.

What you receive each turn: CONTEXT (JSON facts computed by the platform from the owner's broker) and the
recent conversation. Use ONLY those facts. If a number you need is not in CONTEXT, say it is not available.
Never invent prices, levels, news, statistics or results.
CONTEXT is the CURRENT state. Earlier messages may be out of date (e.g. a stop that was later released):
for anything about the present (kill switch, monitor status, prices) trust CONTEXT, not the conversation.
Format: plain text only — no markdown (no **, no #). Short lines; use "-" for lists.
Write every text field (reply AND each action's rationale) in the owner's language.

Hard rules:
- The platform does NOT send orders to the broker. A trade from you is a TRADE IDEA (action propose_trade):
  the platform checks risk and computes the lot size; the owner decides and enters it by hand in MT5.
- Every propose_trade must have a stop-loss (sl) on the correct side of the current price, and a target (tp)
  when you can justify one. Base sl/tp on levels in CONTEXT (recent swing high/low, previous day, ATR).
- Never promise or imply profit. Say how confident you are and what would invalidate the idea.
  Distinguish TESTED evidence (backtest results in CONTEXT) from your own untested reading of the chart.
- If CONTEXT.data_source is MOCK, say clearly that the data is synthetic.
- Paper live monitor = simulation of the tested ORB+Retest strategy. start_live/stop_live control it.
- kill_switch stops everything new (no ideas, monitor off). Use it when the owner asks to stop.
- run_backtest starts a background historical test of the ORB+Retest strategy on an asset.
- Only include actions the owner asked for or clearly agreed to. Analysis alone needs no action.
"""

STOP_PATTERN = re.compile(
    r"^\s*(وقف|اوقف|أوقف|توقف|وقّف|ستوب|stop|kill)(\s+(كل\s*شي|كلشي|الكل|كلشيء|everything|all))?[\s.!،]*$", re.I)


class ChatUnavailable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def find_claude_exe() -> str | None:
    """Newest Claude Code executable on this machine, else `claude` on PATH.

    The Claude desktop app is a packaged (MSIX) Windows app: inside the app, %APPDATA%\\Claude is a
    redirected view of %LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude. A process started
    outside the app (e.g. start-platform.bat) only sees the real Packages path, so both are searched.
    """
    roots = [Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"]
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roots += [Path(p) / "LocalCache" / "Roaming" / "Claude" / "claude-code"
              for p in glob.glob(str(local / "Packages" / "Claude_*"))]
    exes = [e for r in roots for e in glob.glob(str(r / "**" / "claude.exe"), recursive=True)]
    if exes:
        return max(exes, key=os.path.getmtime)
    return shutil.which("claude")


class ClaudeCliRunner:
    def __init__(self, workdir: Path, model: str = "sonnet", timeout_s: int = 150, exe: str | None = None) -> None:
        self.workdir = Path(workdir)
        self.model = model
        self.timeout = timeout_s
        self._exe = exe

    def run(self, system: str, prompt: str, schema: dict) -> dict:
        """Chat turn: {reply, actions, cost_usd, duration_ms}."""
        out = self.run_json(system, prompt, schema)
        data = out["data"]
        return {"reply": str(data.get("reply", "")), "actions": list(data.get("actions") or []),
                "cost_usd": out["cost_usd"], "duration_ms": out["duration_ms"]}

    def run_json(self, system: str, prompt: str, schema: dict) -> dict:
        """Any structured call: {data: <object matching schema>, cost_usd, duration_ms}."""
        exe = self._exe or find_claude_exe()
        if not exe:
            raise ChatUnavailable("CLAUDE_CLI_MISSING", "Claude Code is not installed on this computer.")
        self.workdir.mkdir(parents=True, exist_ok=True)
        cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "",
               "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
               "--model", self.model, "--system-prompt", system]
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               cwd=self.workdir, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ChatUnavailable("CLAUDE_TIMEOUT", f"Claude did not answer within {self.timeout}s.") from None
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise ChatUnavailable("CLAUDE_ERROR", (p.stderr or p.stdout or "no output")[:300]) from None
        result_text = str(out.get("result") or "")
        if out.get("is_error"):
            if "not logged in" in result_text.lower() or "/login" in result_text:
                raise ChatUnavailable("CLAUDE_NOT_LOGGED_IN",
                                      "Claude Code is not logged in on this computer. Open a terminal, run `claude`, then /login.")
            raise ChatUnavailable("CLAUDE_ERROR", result_text[:300] or "Claude returned an error")
        data = out.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(result_text)
            except json.JSONDecodeError:
                data = {"reply": result_text, "actions": []}
        return {"data": data, "cost_usd": out.get("total_cost_usd"), "duration_ms": out.get("duration_ms")}


def validate_actions(raw: list) -> tuple[list[dict], list[str]]:
    """Keep only well-formed, known actions. Model output is untrusted input."""
    ok, dropped = [], []
    for a in raw[:5]:
        if not isinstance(a, dict) or a.get("type") not in ACTION_TYPES:
            dropped.append(str(a)[:80])
            continue
        if a["type"] == "propose_trade":
            if a.get("side") not in ("BUY", "SELL") or not isinstance(a.get("sl"), (int, float)) or not a.get("asset"):
                dropped.append(f"propose_trade without asset/side/sl: {str(a)[:80]}")
                continue
            if a.get("risk_pct") is not None and not (0 < float(a["risk_pct"]) <= 2):
                a = {**a, "risk_pct": None}
        ok.append(a)
    return ok, dropped


class ChatService:
    """Conversation + background jobs. `context_fn(message)` builds the facts; `execute(action)` runs one action."""

    def __init__(self, runner, history_path: Path, context_fn: Callable[[str], dict],
                 execute: Callable[[dict], dict], kill: Callable[[], dict]) -> None:
        self.runner = runner
        self.history_path = Path(history_path)
        self.context_fn = context_fn
        self.execute = execute
        self.kill = kill
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- history
    def history(self, limit: int = 100) -> list[dict]:
        if not self.history_path.exists():
            return []
        with open(self.history_path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return rows[-limit:]

    def _append(self, msg: dict) -> dict:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        return msg

    def clear(self) -> None:
        if self.history_path.exists():
            self.history_path.unlink()

    @staticmethod
    def _msg(role: str, text: str, **extra) -> dict:
        return {"id": uuid.uuid4().hex[:10], "role": role, "text": text,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}

    # ---------------------------------------------------------------- turns
    def submit(self, message: str) -> dict:
        message = message.strip()[:2000]
        if not message:
            raise ValueError("empty message")
        user = self._append(self._msg("user", message))
        if STOP_PATTERN.match(message):
            result = self.kill()
            reply = self._append(self._msg("assistant", "تم الإيقاف فوراً: مفتاح الإيقاف مفعّل والمراقبة الحية متوقفة. "
                                                        "لن تُقترح صفقات جديدة حتى تعيد التفعيل.",
                                           actions=[{"type": "kill_switch", "ok": True, "detail": result}],
                                           deterministic=True))
            return {"job_id": None, "status": "DONE", "messages": [user, reply]}
        with self._lock:
            if any(j["status"] == "RUNNING" for j in self.jobs.values()):
                raise RuntimeError("CHAT_BUSY")
            job_id = uuid.uuid4().hex[:12]
            job = {"job_id": job_id, "status": "RUNNING", "messages": [user], "error": None}
            self.jobs[job_id] = job
        threading.Thread(target=self._work, args=(job, message), daemon=True, name=f"chat-{job_id}").start()
        return {"job_id": job_id, "status": "RUNNING", "messages": [user]}

    def _prompt(self, message: str, context: dict) -> str:
        convo = [{"role": m["role"], "text": m["text"]} for m in self.history(14)[:-1]]
        return ("CONTEXT (facts from the platform, JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)
                + "\n\nRECENT CONVERSATION:\n" + json.dumps(convo, ensure_ascii=False)
                + "\n\nOWNER'S NEW MESSAGE:\n" + message)

    def _work(self, job: dict, message: str) -> None:
        try:
            context = self.context_fn(message)
            out = self.runner.run(SYSTEM_PROMPT, self._prompt(message, context), OUTPUT_SCHEMA)
            actions, dropped = validate_actions(out["actions"])
            results = []
            for a in actions:
                try:
                    results.append({**a, "ok": True, "detail": self.execute(a)})
                except Exception as e:  # noqa: BLE001 — every refusal is shown to the owner
                    results.append({**a, "ok": False, "code": getattr(e, "code", type(e).__name__), "detail": str(e)})
            reply = self._append(self._msg("assistant", out["reply"], actions=results, dropped_actions=dropped,
                                           cost_usd=out.get("cost_usd"), duration_ms=out.get("duration_ms"),
                                           data_source=context.get("connection", {}).get("source")))
            job["messages"].append(reply)
            job["status"] = "DONE"
        except ChatUnavailable as e:
            job["status"], job["error"] = "FAILED", {"code": e.code, "message": str(e)}
        except Exception as e:  # noqa: BLE001
            job["status"], job["error"] = "FAILED", {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"}
