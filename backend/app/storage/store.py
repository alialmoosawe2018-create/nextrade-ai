"""Local JSON stores under data/ (gitignored).

Account profiles have NO password field — the model forbids extra keys, so a
password can never be written to disk through this store.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.brokers.server_time import ServerTz
from app.core.config import CostModel, OrbConfig, RiskConfig, load_yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config"


class ServerTzRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    hours: float = 0.0
    zone: str | None = None
    evidence: str = ""
    decided_at: str = ""

    def to_tz(self) -> ServerTz:
        return ServerTz(self.kind, self.hours, self.zone)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["MT5", "MOCK"] = "MT5"
    server: str = Field(default="", max_length=120)
    login: int | None = Field(default=None, ge=1)
    terminal_path: str | None = Field(default=None, max_length=400)
    symbol: str | None = None
    server_tz: ServerTzRecord | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class PaperSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starting_balance: float = Field(10_000.0, gt=0, le=10_000_000)


class _JsonFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self, default):
        if not self.path.exists():
            return default
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write(self, data) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)


class ProfileStore:
    def __init__(self, data_dir: Path) -> None:
        self._f = _JsonFile(Path(data_dir) / "accounts.json")
        self._lock = threading.Lock()

    def all(self) -> list[Profile]:
        return [Profile(**p) for p in self._f.read([])]

    def get(self, pid: str) -> Profile | None:
        return next((p for p in self.all() if p.id == pid), None)

    def save(self, profile: Profile) -> Profile:
        with self._lock:
            items = [p for p in self.all() if p.id != profile.id] + [profile]
            self._f.write([p.model_dump(mode="json") for p in items])
        return profile

    def delete(self, pid: str) -> bool:
        with self._lock:
            items = self.all()
            keep = [p for p in items if p.id != pid]
            self._f.write([p.model_dump(mode="json") for p in keep])
            return len(keep) != len(items)


SETTINGS_MODELS = {"strategy": OrbConfig, "risk": RiskConfig, "costs": CostModel, "paper": PaperSettings}
DEFAULT_FILES = {"strategy": "strategy_orb.yaml", "risk": "risk.yaml", "costs": "costs.yaml"}


class SettingsStore:
    """Defaults come from config/*.yaml; the owner's edits are saved under data/settings/."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "settings"

    def default(self, name: str) -> BaseModel:
        model = SETTINGS_MODELS[name]
        if name in DEFAULT_FILES:
            return model(**load_yaml(CONFIG / DEFAULT_FILES[name]))
        return model()

    def get(self, name: str) -> BaseModel:
        f = _JsonFile(self.dir / f"{name}.json")
        data = f.read(None)
        return SETTINGS_MODELS[name](**data) if data is not None else self.default(name)

    def put(self, name: str, data: dict) -> BaseModel:
        model = SETTINGS_MODELS[name](**data)  # validates; raises on bad values
        _JsonFile(self.dir / f"{name}.json").write(model.model_dump(mode="json"))
        return model

    def reset(self, name: str) -> BaseModel:
        p = self.dir / f"{name}.json"
        if p.exists():
            p.unlink()
        return self.default(name)
