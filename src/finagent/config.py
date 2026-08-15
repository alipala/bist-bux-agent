"""Ayar yukleme: config/*.yaml + .env"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _deep_get(d: dict, path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


@dataclass
class Settings:
    raw: dict = field(default_factory=dict)
    selectors: dict = field(default_factory=dict)
    root: Path = ROOT

    # --- kisayollar ---
    def get(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.raw, path, default)

    def sel(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.selectors, path, default)

    @property
    def db_path(self) -> Path:
        p = os.getenv("DB_PATH") or "data/finagent.db"
        return self._resolve(p)

    @property
    def profile_dir(self) -> Path:
        p = os.getenv("BROWSER_PROFILE_DIR") or self.get("browser.profile_dir", ".browser_profile")
        return self._resolve(p)

    @property
    def report_dir(self) -> Path:
        return self._resolve(self.get("report.output_dir", "reports"))

    @property
    def discovery_dir(self) -> Path:
        return self._resolve("discovery")

    @property
    def bist_watchlist(self) -> list[str]:
        return [s.strip().upper() for s in (self.get("watchlist.bist") or [])]

    @property
    def bux_watchlist(self) -> list[str]:
        return [s.strip().upper() for s in (self.get("watchlist.bux") or [])]

    def source_enabled(self, name: str) -> bool:
        return bool(self.get(f"sources.{name}.enabled", False))

    def _resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.root / p)

    # --- gizli degerler (sadece .env'den) ---
    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        v = os.getenv(name)
        return v if v not in (None, "") else default


def _auth_modunu_uygula(raw: dict) -> str:
    """
    LLM kimlik yolunu secer: Claude ABONELIGI mi, API ANAHTARI mi?

    Claude Code CLI kimlik sirasi: ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN
    -> OAuth profili (claude.ai girisi). Yani .env'de bir API anahtari varsa
    Max/Pro ABONELIGI HIC KULLANILMAZ — anahtar onu golgeler ve her cagri
    token basina API kredisinden duser.

    Olculdu: anahtar setliyken cagri "kredi bitti" hatasi verdi; anahtar
    ortamdan silinince ayni cagri abonelik uzerinden calisti.

    DIKKAT: anahtari BOSALTMAK yetmez. Bos bir ANTHROPIC_API_KEY de kendi
    sirasini kazanir ve bos anahtarla kimlik dogrulamaya calisir — silinmeli.
    """
    mod = str(_deep_get(raw, "analysis.llm.auth", "abonelik") or "abonelik").lower()
    if mod in ("abonelik", "subscription", "oauth"):
        os.environ.pop("ANTHROPIC_API_KEY", None)
    return mod


def load_settings(root: Path | None = None) -> Settings:
    root = root or ROOT
    load_dotenv(root / ".env")

    with open(root / "config" / "settings.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    _auth_modunu_uygula(raw)

    sel_path = root / "config" / "selectors.yaml"
    selectors = {}
    if sel_path.exists():
        with open(sel_path, encoding="utf-8") as f:
            selectors = yaml.safe_load(f) or {}

    s = Settings(raw=raw, selectors=selectors, root=root)
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    s.report_dir.mkdir(parents=True, exist_ok=True)
    return s
